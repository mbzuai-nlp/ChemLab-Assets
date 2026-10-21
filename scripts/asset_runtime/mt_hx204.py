"""CHEM-002 HX204: public boiling points plus explicitly synthetic drying kinetics.

Terminal layer (1.1.0): the HX204 touch terminal of the reference manual (references/manuals/mt_hx204/operating_manual__hx204_reference_manual.pdf,
software V 3.30; 'p.N' below is the PDF page, printed page = N - 2) on top of the drying model: methods (drying programs, switch-off criteria,
display modes, start weight, control limits, resolution, free factor, QuickPredict, workflow handling, versioning), the measurement work flow
(tare, start, stop, result, note, identifications), results, tests and adjustments (weight and temperature), settings, users / groups / login,
and the front panel (panel.*: panel_ui.json beside the contract, run by the shared engine _panel_ui.py).  The direct capabilities of 1.0.0 keep
their meaning; the terminal commands are new.  Sensor error parameters (balance span / zero / internal weight, temperature offset / gain) are
host-settable and 0 (ideal) by default; adjustments store a correction that the readings and the heater control use.
1.2.1 (sync audit): the 1.0.0 direct run also heats on the corrected sensor and reports loss against the indicated start weight; a drying started
from the terminal belongs to the calling session and another agent cannot stop it; the default settings give the logged-out and new-user
preferences; the time zone shifts the shown clock.
1.3.0 (sweep 2026-10-19): evaluator fault.inject / fault.clear for the error messages of manual 9.1 (Overload, Underload, the blinking zeros of
the zero range, No sensor connected.): the work screen shows the message instead of the weight, taring and starting are refused with the
manual text, No sensor connected ends a running drying (no weight values); the remedy (reduce the weight, insert the pan holder, remove the
weight and retry, connect the cable) is the evaluator's fault.clear."""
from __future__ import annotations
import bisect
import copy
from asset_runtime import _annotations as _ann
import datetime
import hashlib
import json
import math
import os
import tempfile
import weakref
import zoneinfo
from pathlib import Path

PROTOCOL_VERSION='1.0.0'
ASSET_TYPE_ID='instrument.mt_hx204'
ASSET_VERSION='1.3.0'
CATALOG_ID='CHEM-002'
ROOT=Path(__file__).resolve().parents[2]
ASSET_DIR=ROOT/'assets/expansion_batch04/t4_measure/mt_hx204'
TEMPLATE_REL='assets/reactions/mt_hx204_moisture_library/templates.json'

class ActionRejected(ValueError):
    """Stable errors never disclose sample composition."""

def number(unit,lo=None,hi=None):
    d={'type':'number','x-unit':unit}
    if lo is not None: d['minimum']=lo
    if hi is not None: d['maximum']=hi
    return d

def obj(props,optional=()):
    return dict(type='object',properties=props,required=[k for k in props if k not in optional],additionalProperties=False)

BOOL={'type':'boolean'}
TEXT={'type':'string'}
PHASE={'type':'string','enum':['idle','running','succeeded','cancelled']}
MODE={'type':'string','enum':['standard','rapid','gentle','steps']}
BASIS={'type':'string','enum':['wet','dry']}
POINT=obj({'time_s':number('s',0),'mass_kg':number('kg',0),'loss_fraction':number('1',0,1)})
RESULT=obj({'phase':PHASE,'mass_kg':number('kg',0),'loss_fraction':number('1',0,1),
 'reported_ratio':{'anyOf':[number('1',0),{'type':'null'}]},'basis':BASIS,
 'curve':{'type':'array','items':POINT},'template_source':TEXT,
 'evidence':{'type':'string','const':'synthetic'},
 'interpretation':{'type':'string','const':'loss_on_drying_not_water_specific'}})

# input, output, components, meaning, kind, visibility, example, effects
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','display_digits'],'供电设定；断电取消采集并保留失重','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'thermal.set_temperature':(obj({'temperature_k':number('K',313.15,503.15)}),obj({'temperature_k':number('K',313.15,503.15)}),['heater','temperature_sensor','display_digits'],'整数摄氏度目标写入；实际升温只随step演化','device_command','agent',{'temperature_k':378.15},['thermal.target_k']),
 'method.configure':(obj({'mode':MODE,'duration_s':number('s',60,28800),'basis':BASIS}),obj({'mode':MODE,'duration_s':number('s',60,28800),'basis':BASIS}),['terminal','display_digits'],'升温模式工程代理、定时终点、湿基/干基结果设置','configuration','agent',{'mode':'standard','duration_s':60,'basis':'wet'},['method.mode','method.duration_s','method.basis']),
 'lid.set_position':(obj({'position_m':number('m',0,.1085)}),obj({'position_m':number('m',0,.1085)}),['lid','heater'],'宿主设置升降盖位置；采集中不得打开','device_command','agent',{'position_m':.1085},['lid.position_m']),
 'sample.configure':(obj({'species_kg':{'type':'object','additionalProperties':number('kg',0,.201)},'residue_kg':number('kg',0,.201)}),obj({'configured':BOOL}),['sample_pan','balance_sensor'],'受信任宿主初始化样品账本；非机器人投料','configuration','evaluator',{'species_kg':{'ethanol':.001},'residue_kg':.001},['sample.initial_kg','sample.remaining_kg','sample.evaporated_kg','balance.indicated_kg','cycle.phase']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['heater','sample_pan','balance_sensor','display_digits'],'启动干燥；必须由宿主推进时间完成','device_command','agent',{},['cycle.phase','cycle.elapsed_s']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['heater','display_digits'],'取消并保留已蒸发质量与曲线','device_command','agent',{},['cycle.phase']),
 'moisture.measure':(obj({}),RESULT,['sample_pan','balance_sensor','display_digits'],'纯读取质量曲线与失重率；不识别化学水','query','agent',{},[]),
}
LEGACY_CAPS=tuple(CAPS)
# schema, unit, origin, visibility, components, writable capability/argument
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'thermal.target_k':(number('K',313.15,503.15),'K','setpoint','agent',['heater'],('thermal.set_temperature','temperature_k')),
 'thermal.temperature_k':(number('K',0),'K','process_truth','evaluator',['heater'],None),
 'thermal.indicated_temperature_k':(number('K',0),'K','derived','agent',['temperature_sensor','display_digits'],None),
 'lid.position_m':(number('m',0,.1085),'m','scene','agent',['lid'],('lid.set_position','position_m')),
 'method.mode':(MODE,'1','setpoint','agent',['terminal'],None),
 'method.duration_s':(number('s',60,28800),'s','setpoint','agent',['terminal'],None),
 'method.basis':(BASIS,'1','setpoint','agent',['terminal'],None),
 'cycle.phase':(PHASE,'1','derived','agent',['display_digits'],None),
 'cycle.elapsed_s':(number('s',0),'s','derived','agent',['display_digits'],None),
 'balance.indicated_kg':(number('kg',0),'kg','derived','agent',['balance_sensor','display_digits'],None),
 'sample.initial_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_pan'],None),
 'sample.remaining_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_pan'],None),
 'sample.evaporated_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_pan'],None),
}
LEGACY_SLOTS=tuple(SLOTS)

def validate(schema,value):
    typ=schema.get('type')
    if isinstance(typ,list):                                                  # nullable schemas of the terminal commands
        if value is None and 'null' in typ: return
        typ=next(t for t in typ if t!='null'); schema=dict(schema,type=typ)
    if typ=='number' or typ=='integer':
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value): raise ActionRejected('INVALID_ARGUMENT')
        if typ=='integer' and float(value)!=int(value): raise ActionRejected('INVALID_ARGUMENT')
        if value<schema.get('minimum',-math.inf) or value>schema.get('maximum',math.inf): raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='boolean' and type(value) is not bool: raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='string' and not isinstance(value,str): raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='string' and not schema.get('minLength',0)<=len(value)<=schema.get('maxLength',math.inf): raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='null' and value is not None: raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='array':
        if not isinstance(value,list) or not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',math.inf): raise ActionRejected('INVALID_ARGUMENT')
        for x in value: validate(schema.get('items',{}),x)
    elif typ=='object':
        if not isinstance(value,dict): raise ActionRejected('INVALID_ARGUMENT')
        props=schema.get('properties',{})
        if not set(schema.get('required',[]))<=value.keys(): raise ActionRejected('INVALID_ARGUMENT')
        for key,val in value.items():
            if not isinstance(key,str): raise ActionRejected('INVALID_ARGUMENT')
            sub=props.get(key,schema.get('additionalProperties',True))
            if sub is False: raise ActionRejected('INVALID_ARGUMENT')
            if isinstance(sub,dict): validate(sub,val)
    if 'enum' in schema and value not in schema['enum']: raise ActionRejected('INVALID_ARGUMENT')


# >>> CHEM-002 HX204 terminal
# ---------------------------------------------------------------- terminal tables (shared with the panel spec builder)
HX_PROGRAMS=('Standard','Rapid','Gentle','Steps')
HX_SOCS=('1','2','3','4','5','Timed','Free (mg / s)','Free (% / s)')
SOC_DT={'1':10.,'2':20.,'3':50.,'4':90.,'5':140.}                                   # 1 mg per dt (p.40)
SOC_TEXT={'1':'1 (1 mg / 10 s)','2':'2 (1 mg / 20 s)','3':'3 (1 mg / 50 s)','4':'4 (1 mg / 90 s)','5':'5 (1 mg / 140 s)','Timed':'Timed...',
          'Free (mg / s)':'Free (mg / s)...','Free (% / s)':'Free (% / s)...'}
HX_DISPLAY=('%MC','%DC','%AM','%AD','g','g/kg MC','g/kg DC','-%MC')
ATRO=('%AM','%AD')
HX_LANGUAGES=('English','Deutsch','Italiano','Français','Español','Português','Polski','Magyar','Český','Pусский','日本語','中文','한국어/조선말')
HX_KEYBOARDS=HX_LANGUAGES[:9]
HX_DATE_FORMATS=('D. MMM YYYY','MMM D YYYY','DD.MM.YYYY','MM/DD/YYYY','YYYY/MM/DD','YYYY-MM-DD')
HX_TIME_FORMATS=('24:MM','12:MM','24.MM','12.MM')
HX_SCHEMES=('Blue','Green','Orange','Grey')
HX_TIME_ZONES=('UTC','Europe/Zurich','Europe/London','America/New_York','Asia/Shanghai','Asia/Tokyo')
HX_PRINTERS=('None','P-2x Strip Printer','P-4x Strip Printer','P-5x Strip Printer','Local PCL printer','Network PCL printer','EasyDirect Moisture')
HX_PRINT_CONTENT=('Head','S/N instrument and software','User name','Company information','Adjustment info','Method parameters',"ID's",'Measurement',
                  'Signature 1','Signature 2')
HX_RIGHTS=(('user_preferences','User preferences'),('instrument_settings','Instrument settings'),('edit_shortcuts','Edit shortcuts'),
           ('method_definition','Method Definition'),('select_methods','Select methods'),('application_settings','Application settings'),
           ('run_tests','Run tests'),('run_adjustments','Run adjustments'),('quality_management','Quality management'),
           ('user_management','User management'),('system_data_management','System and data management'))
RIGHT_KEYS=tuple(k for k,_ in HX_RIGHTS)
GROUP_DEFAULTS={                                                                      # p.104: four groups with pre-defined rights (assumption: which)
    'Administrators':set(RIGHT_KEYS),
    'Supervisors':set(RIGHT_KEYS)-{'user_management','system_data_management'},
    'Quality Managers':{'user_preferences','edit_shortcuts','method_definition','select_methods','run_tests','run_adjustments','quality_management'},
    'Operators':{'user_preferences','edit_shortcuts'},
}
SERIAL='B922012345'; SOFTWARE='V 3.30'; INTERNAL_WEIGHT_G=100.0; MAX_METHODS=300; MAX_USERS=30; MAX_GROUPS=10; MAX_IDS=10; MAX_HISTORY=50
CLOCK0=datetime.datetime(2026,10,18,9,0,0)                                            # assumption: the simulated clock starts here
CLOCK0_ZONE='Europe/Zurich'                                                             # CLOCK0 is the local time of the factory time zone
_ZONE_FALLBACK_H={'UTC':0,'Europe/Zurich':2,'Europe/London':1,'America/New_York':-4,'Asia/Shanghai':8,'Asia/Tokyo':9}   # without tz data (Oct.)
def _zone(name):
    try: return zoneinfo.ZoneInfo(name)
    except Exception: return datetime.timezone(datetime.timedelta(hours=_ZONE_FALLBACK_H.get(name,0)))
CLOCK0_UTC=CLOCK0.replace(tzinfo=_zone(CLOCK0_ZONE)).astimezone(datetime.timezone.utc)


def _f(key,label,typ,default,page,**kw): return dict(key=key,label=label,type=typ,default=default,page=page,**kw)
def _b(key,label,default,page,**kw): return _f(key,label,'bool',default,page,**kw)
def _e(key,label,options,default,page,**kw): return _f(key,label,'enum',default,page,options=list(options),**kw)
def _n(key,label,lo,hi,default,page,unit=None,integer=False,**kw): return _f(key,label,'int' if integer else 'num',default,page,min=lo,max=hi,unit=unit,**kw)
def _s(key,label,lo,hi,default,page,**kw): return _f(key,label,'text',default,page,min_len=lo,max_len=hi,**kw)

DRY=('Standard','Rapid','Gentle')
# SmartCal (p.81-83, p.103-104): four fixed methods with the recommended control limits (standard / cSmartCal), two custom methods
SC_FIXED=(70,100,130,160)
SC_LIMITS={70:(3.2,4.4),100:(5.2,6.4),130:(7.4,8.8),160:(9.9,11.7)}
SC_CLIMITS={70:(3.3,4.3),100:(5.3,6.3),130:(7.5,8.7),160:(10.0,11.6)}
SC_TEST_S=600.0                    # assumption: the SmartCal method dries for a fixed 10 min (the manual: 'a specific temperature for a specific time')
SC_TAU_S=60.0                      # assumption: time constant of the moisture release of the granulate
SC_RH_COEF,SC_RH_REF=0.03,50.0     # assumption: %MC per %rH of the storage humidity; the result is normalised to 50 %rH with the entered humidity
SC_HISTORY=30                      # p.82: at most 30 results per SmartCal test
# Method Wizard / temperature assistant / QuickPredict Wizard (p.51-55, p.60-61): engineering rules (the manual does not print them)
TA_START_C,TA_RATE_C_MIN=40.0,10.0 # temperature assistant: linear ramp from 40 degC to the accepted maximum, then hold until SOC 3
QP_COLD_C,QP_WARM_HOLD_S,QP_MIN_S=40.0,300.0,120.0
SOC_FIELDS=[                                                                         # the switch-off criterion and its parameters (also per drying step)
    _e('soc','Switch-off criterion',HX_SOCS,'3','p.37-41'),
    _n('soc_time_s','Drying time',30,28800,900,'p.41; MT-SICS HA61 (timer 30-28800 s)',unit='s',integer=True,vis=[('soc','in',('Timed',))]),
    _n('soc_free_mg','Weight loss',0.1,10.0,1.0,'p.38-39',unit='mg',vis=[('soc','in',('Free (mg / s)',))],assumption='entry range not given in the manual'),
    _n('soc_free_mg_dt','Time interval',5,180,50,'p.38-39',unit='s',integer=True,vis=[('soc','in',('Free (mg / s)',))],
       assumption='entry range not given in the manual'),
    _n('soc_free_pct','Weight loss',0.001,1.0,0.01,'p.39',unit='%',vis=[('soc','in',('Free (% / s)',))],assumption='entry range not given in the manual'),
    _n('soc_free_pct_dt','Time interval',5,180,50,'p.39',unit='s',integer=True,vis=[('soc','in',('Free (% / s)',))],
       assumption='entry range not given in the manual'),
]
STEP_FIELDS=[_n('temperature','Temperature',40,230,105,'p.39',unit='°C',integer=True)]+[dict(f) for f in SOC_FIELDS]
STEP_DEFAULTS=[dict(temperature=50,soc='Timed',soc_time_s=300,soc_free_mg=1.0,soc_free_mg_dt=50,soc_free_pct=0.01,soc_free_pct_dt=50),
               dict(temperature=105,soc='3',soc_time_s=900,soc_free_mg=1.0,soc_free_mg_dt=50,soc_free_pct=0.01,soc_free_pct_dt=50)]   # p.38
LIM=lambda k,label: _n(k,label,0.01,1000.0,None,'p.45-46',nullable=True,vis=[('limits','in',(True,))],assumption='0.01-100 (1000 in %AM / %AD)')  # noqa: E731
HX_METHOD_FIELDS=[
    _s('name','Method name',1,30,'','p.50, p.52',section='general'),
    _e('program','Drying program',HX_PROGRAMS,'Standard','p.35',section='main'),
    _n('temperature','Drying temperature',40,230,105,'p.36-38',unit='°C',integer=True,section='main',vis=[('program','in',DRY)]),
    _n('ramp_s','Ramp time',0,28800,180,'p.37-38; MT-SICS HA61 (ramp 0-28800 s)',unit='s',integer=True,section='main',vis=[('program','in',('Gentle',))]),
]+[dict(f,section='main',vis=[('program','in',DRY)]+f.get('vis',[])) for f in SOC_FIELDS]+[
    _b('soc_delay','SOC delay',False,'p.41',section='main',vis=[('program','in',('Standard',))]),
    _n('soc_delay_min','Delay',0,99,0,'p.41',unit='min',integer=True,section='main',vis=[('program','in',('Standard',)),('soc_delay','in',(True,))]),
    _e('display_mode','Display mode',HX_DISPLAY,'%MC','p.41-44',section='main'),
    _b('start_weight','Start weight',False,'p.44',section='main'),
    _n('start_weight_g','Target weight',0.1,200.0,2.0,'p.44',unit='g',section='main',vis=[('start_weight','in',(True,))],
       assumption='target shown when the start weight is switched on (most samples 2-5 g, p.44)'),
    _n('start_weight_tol','Start weight tolerance',1,25,10,'p.44',unit='%',integer=True,section='main',vis=[('start_weight','in',(True,))]),
    _e('weigh_in_aid','Weigh-in aid',('Passive','Active'),'Passive','p.44',section='main',vis=[('start_weight','in',(True,))]),
    _b('limits','Control limits',False,'p.45',section='result'),
    dict(LIM('t1_plus','T1+ (Upper warning limit)'),section='result'),dict(LIM('t1_minus','T1- (Lower warning limit)'),section='result'),
    dict(LIM('t2_plus','T2+ (Upper intervention limit)'),section='result'),dict(LIM('t2_minus','T2- (Lower intervention limit)'),section='result'),
    _e('resolution','Resolution',('Standard','High'),'Standard','p.46-47',section='result',vis=[('display_mode','not_in',ATRO)]),
    _b('free_factor','Free factor',False,'p.47',section='result',vis=[('display_mode','not_in',('g',))]),
    _n('factor','Factor',-10.0,10.0,1.0,'p.47',section='result',vis=[('display_mode','not_in',('g',)),('free_factor','in',(True,))]),
    _n('offset','Offset',-1000.0,1000.0,0.0,'p.47',section='result',vis=[('display_mode','not_in',('g',)),('free_factor','in',(True,))]),
    _e('format','Format',('x','x.x','x.xx','x.xxx'),'x.xxx','p.47',section='result',vis=[('display_mode','not_in',('g',)),('free_factor','in',(True,))]),
    _b('qp','QuickPredict',False,'p.45, p.57-58',section='qp'),
    _e('qp_usage','QuickPredict usage',('Inactive','Active'),'Inactive','p.45, p.61',section='qp',vis=[('qp','in',(True,))]),
    _b('qp_allow_save','Allow saving of prediction',False,'p.45, p.67',section='qp',vis=[('qp','in',(True,))]),
    _n('qp_save_threshold','Threshold to allow saving',0.0,100.0,None,'p.45, p.68',nullable=True,section='qp',vis=[('qp','in',(True,))]),
    _b('qp_allow_stop','Allow stopping of measurement',False,'p.46, p.68',section='qp',vis=[('qp','in',(True,))]),
    _n('qp_stop_threshold','Threshold to allow stopping',0.0,100.0,None,'p.46, p.68',nullable=True,section='qp',vis=[('qp','in',(True,))]),
    _e('starting_mode','Starting mode',('Automatic','Manual'),'Automatic','p.48',section='workflow'),
    _e('preheating','Preheating',('OFF','Manual','Standby'),'OFF','p.48-49',section='workflow'),
    _n('preheat_temp','Temperature',40,100,40,'p.49',unit='°C',integer=True,section='workflow',vis=[('preheating','in',('Manual','Standby'))]),
    _n('preheat_duration_min','Duration',1,1440,180,'p.49',unit='min',integer=True,section='workflow',vis=[('preheating','in',('Standby',))],
       assumption='entry range not given (factory 3 h)'),
    _f('preheat_turnoff','Turn-off time','time',None,'p.49',section='workflow',nullable=True,vis=[('preheating','in',('Standby',))]),
    _e('accessory','Accessories',('Sample pan','HA-Cage'),'Sample pan','p.49',section='workflow'),
    _b('acc_filter','Glass fibre filter',False,'p.50',section='workflow',vis=[('accessory','in',('Sample pan',))]),
    _b('acc_filter2','Second glass fibre filter',False,'p.50',section='workflow',vis=[('accessory','in',('Sample pan',))]),
    _b('acc_downholder','Downholder',False,'p.50',section='workflow',vis=[('accessory','in',('Sample pan',))]),
    _s('sample_preparation','Sample preparation',0,100,'','p.50',section='general'),
]
HX_METHOD_FIELD={f['key']:f for f in HX_METHOD_FIELDS}
RESULT_KEYS=('program','temperature','ramp_s','soc','soc_time_s','soc_free_mg','soc_free_mg_dt','soc_free_pct','soc_free_pct_dt','soc_delay',
             'soc_delay_min','display_mode','start_weight','start_weight_g','start_weight_tol','weigh_in_aid','resolution','free_factor','factor','offset',
             'starting_mode','preheating','preheat_temp','accessory','acc_filter','acc_filter2','acc_downholder','steps')   # p.55: influence the result

LANG9=HX_LANGUAGES[:9]
HX_PREF_FIELDS=[
    _e('language','Display language',HX_LANGUAGES,'English','p.86'),_e('keyboard','Keyboard language',HX_KEYBOARDS,'English','p.86'),
    _n('brightness','Brightness',10,100,80,'p.87',unit='%',integer=True),_e('scheme','Color scheme',HX_SCHEMES,'Blue','p.87'),
    _b('sound','Sound',False,'p.87'),_n('volume','Volume',0,100,50,'p.87',unit='%',integer=True,vis=[('sound','in',(True,))]),
]
IP=dict(pattern=r'^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$')
_P='printer.device'
HX_SETTINGS=[
    # Instrument settings (p.88-93): right instrument_settings
    _e('region.config','Configuration',('Manual','Automatic'),'Manual','p.88',section='region',right='instrument_settings'),
    _s('region.server','Server',0,64,'','p.88',section='region',right='instrument_settings',vis=[('region.config','in',('Automatic',))]),
    _e('region.time_zone','Time zone',HX_TIME_ZONES,'Europe/Zurich','p.88',section='region',right='instrument_settings',
       assumption='a short list of zones; the clock runs in UTC and the zone sets the shown local time (factory zone Europe/Zurich)'),
    _e('region.date_format','Date format',HX_DATE_FORMATS,'D. MMM YYYY','p.88',section='region',right='instrument_settings'),
    _e('region.time_format','Time format',HX_TIME_FORMATS,'24:MM','p.88',section='region',right='instrument_settings'),
    _e(_P,'Printer',HX_PRINTERS,'None','p.88-89 (no printer installed by default)',section='peripherals',right='instrument_settings'),
    _e('printer.interface','Device interface',('RS232','USB','Network'),'RS232','p.89-90',section='peripherals',right='instrument_settings',vis=[(_P,'not_in',('None',))]),
    _s('printer.name','Device Name',0,30,'','p.89-90',section='peripherals',right='instrument_settings',vis=[(_P,'not_in',('None',))]),
    _e('printer.port','Device Port',('COM1','SOU'),'COM1','p.89',section='peripherals',right='instrument_settings',vis=[(_P,'not_in',('None',)),('printer.interface','in',('RS232',))]),
    _e('printer.baud','Baudrate',(1200,2400,4800,9600,19200,38400,57600,115200),9600,'p.89',section='peripherals',right='instrument_settings',
       vis=[(_P,'not_in',('None',)),('printer.interface','in',('RS232',))]),
    _e('printer.databits','DataBits',(7,8),8,'p.89',section='peripherals',right='instrument_settings',vis=[(_P,'not_in',('None',)),('printer.interface','in',('RS232',))]),
    _e('printer.parity','Parity',('None','Odd','Even'),'None','p.89',section='peripherals',right='instrument_settings',vis=[(_P,'not_in',('None',)),('printer.interface','in',('RS232',))]),
    _e('printer.stopbits','StopBits',('1 StopBit','2 StopBits'),'1 StopBit','p.89',section='peripherals',right='instrument_settings',
       vis=[(_P,'not_in',('None',)),('printer.interface','in',('RS232',))]),
    _e('printer.flow','Flow Control',('None','Xon/Xoff'),'Xon/Xoff','p.89',section='peripherals',right='instrument_settings',vis=[(_P,'not_in',('None',)),('printer.interface','in',('RS232',))]),
    _s('printer.ip','IP address',7,15,'192.168.0.4','p.90',section='peripherals',right='instrument_settings',vis=[(_P,'not_in',('None',)),('printer.interface','in',('Network',))],**IP),
    _n('printer.net_port','Port',1024,65535,9100,'p.90',integer=True,section='peripherals',right='instrument_settings',vis=[(_P,'not_in',('None',)),('printer.interface','in',('Network',))]),
    _b('printer.enabled','Enabled',False,'p.89 (activate the interface with ON)',section='peripherals',right='instrument_settings',vis=[(_P,'not_in',('None',))]),
    _b('sics.enabled','MT-SICS host connection',True,'p.88 (enabled by default)',section='peripherals',right='instrument_settings',
       assumption='stored only: no MT-SICS host computer is connected (RS232 / USB / network) in the simulation; the session API is not MT-SICS'),
    _s('net.host_name','Host name',1,32,'HX204-'+SERIAL,'p.92 (model name and serial number)',section='network',right='instrument_settings'),
    _e('net.config','Configuration',('DHCP assignment','Manual assignment'),'DHCP assignment','p.93',section='network',right='instrument_settings'),
    _s('net.ip','IP address',7,15,'192.168.2.100','p.93',section='network',right='instrument_settings',vis=[('net.config','in',('Manual assignment',))],**IP),
    _s('net.subnet','Subnet',7,15,'255.255.255.0','p.93',section='network',right='instrument_settings',vis=[('net.config','in',('Manual assignment',))],**IP),
    _s('net.gateway','Default Gateway',7,15,'192.168.2.1','p.93',section='network',right='instrument_settings',vis=[('net.config','in',('Manual assignment',))],**IP),
    _e('default.language','Default display language',HX_LANGUAGES,'English','p.90',section='defaults',right='instrument_settings'),
    _e('default.keyboard','Default keyboard language',HX_KEYBOARDS,'English','p.91',section='defaults',right='instrument_settings'),
    _n('default.brightness','Brightness',10,100,80,'p.91',unit='%',integer=True,section='defaults',right='instrument_settings'),
    _e('default.scheme','Color scheme',HX_SCHEMES,'Blue','p.91',section='defaults',right='instrument_settings'),
    _s('shared.path','Path',0,64,'','p.91',section='shared',right='instrument_settings'),
    _s('shared.name','Mapped folder name',0,30,'','p.91',section='shared',right='instrument_settings'),
    _s('shared.user','User name',0,30,'','p.91',section='shared',right='instrument_settings'),
    _s('shared.password','Password',0,30,'','p.91',section='shared',right='instrument_settings',secret=True),
    _s('company.name','Company name',0,30,'','p.91',section='company',right='instrument_settings'),
    _s('company.department','Department',0,30,'','p.91',section='company',right='instrument_settings'),
    _s('company.instrument_id','Instrument ID',0,30,'','p.91',section='company',right='instrument_settings'),
    _s('company.location','Instrument location',0,30,'','p.91',section='company',right='instrument_settings'),
    _s('company.responsible','Instrument responsible',0,30,'','p.92',section='company',right='instrument_settings'),
    # Application settings (p.94-98): right application_settings
    _e('ids.input','Identifications input',('OFF','Free input','Guided input'),'OFF','p.94',section='ids',right='application_settings'),
    _f('ids.clear','Clear input after measurement','multi',[],'p.94',section='ids',right='application_settings',options_from='ids'),
    _e('print.type','Printout type',('Standard','Short','Custom 1','Custom 2'),'Standard','p.95',section='print_strip',right='application_settings'),
    _e('print.language','Printout language',LANG9,'English','p.95',section='print_strip',right='application_settings'),
    _b('print.auto','Print results automatically after measurement',False,'p.95',section='print_strip',right='application_settings'),
    _e('print.interval','Include intermediate results to printout',('OFF','10 s','30 s','1 min','2 min','10 min'),'OFF','p.95',section='print_strip',
       right='application_settings'),
    _f('print.content','Printout content','multi',list(HX_PRINT_CONTENT[:8]),'p.96',section='print_strip',right='application_settings',
       options=list(HX_PRINT_CONTENT),vis=[('print.type','in',('Custom 1','Custom 2'))]),
    _e('a4.language','Printout language',HX_LANGUAGES,'English','p.96',section='print_a4',right='application_settings'),
    _b('a4.auto','Print results automatically after measurement',False,'p.97',section='print_a4',right='application_settings'),
    _s('export.prefix','File name prefix',0,30,'','p.97',section='export',right='application_settings'),
    _e('export.type','File type',('CSV','PDF'),'CSV','p.97',section='export',right='application_settings'),
    _e('export.language','Printout language',HX_LANGUAGES,'English','p.97',section='export',right='application_settings'),
    _b('export.auto','Export results automatically',False,'p.97',section='export',right='application_settings'),
    _e('export.target','Export target',('Peripheral','Network (SFTP)'),'Peripheral','p.98',section='export',right='application_settings'),
    _s('export.ip','IP address',0,15,'','p.98',section='export',right='application_settings',vis=[('export.target','in',('Network (SFTP)',))]),
    _s('export.subdir','Subdirectory',0,30,'','p.98',section='export',right='application_settings',vis=[('export.target','in',('Network (SFTP)',))]),
    _n('export.port','Port',1,65535,22,'p.98',integer=True,section='export',right='application_settings',vis=[('export.target','in',('Network (SFTP)',))]),
    _s('export.user','User name',0,30,'','p.98',section='export',right='application_settings',vis=[('export.target','in',('Network (SFTP)',))]),
    _s('export.password','Password',0,30,'','p.98',section='export',right='application_settings',secret=True,vis=[('export.target','in',('Network (SFTP)',))]),
    # Quality management (p.99-103): right quality_management
    _s('tw1.name','Test weight name',0,30,'Test weight 1','p.100',section='equipment',right='quality_management'),
    _n('tw1.nominal','Nominal weight',0.1,201.0,100.0,'p.100',unit='g',section='equipment',right='quality_management',assumption='100 g preset'),
    _s('tw1.id','Identification',0,30,'','p.100',section='equipment',right='quality_management'),
    _s('tw2.name','Test weight name',0,30,'Test weight 2','p.100',section='equipment',right='quality_management'),
    _n('tw2.nominal','Nominal weight',0.1,201.0,50.0,'p.100',unit='g',section='equipment',right='quality_management',assumption='50 g preset'),
    _s('tw2.id','Identification',0,30,'','p.100',section='equipment',right='quality_management'),
    _s('tkit.name','Temperature kit name',0,30,'Temperature kit','p.100',section='equipment',right='quality_management'),
    _s('tkit.id','Identification',0,30,'','p.100',section='equipment',right='quality_management'),
    _s('rht.id','External humidity sensor ID',0,30,'','p.100',section='equipment',right='quality_management'),
    _n('wt_int.tol','Tolerance (weight test - internal)',0.0001,0.1,0.001,'p.100',unit='%',section='tests',right='quality_management'),
    _e('wt_ext.weight','Test weight (weight test - external)',('Test weight 1','Test weight 2'),'Test weight 1','p.100',section='tests',right='quality_management'),
    _n('wt_ext.tol','Tolerance (weight test - external)',0.001,0.1,0.001,'p.101',unit='g',section='tests',right='quality_management'),
    _b('tt.t1','Temperature 1',True,'p.101',section='tests',right='quality_management'),
    _n('tt.t1_tol','Tolerance',1,5,3,'p.101',unit='°C',integer=True,section='tests',right='quality_management',vis=[('tt.t1','in',(True,))]),
    _b('tt.t2','Temperature 2',True,'p.101',section='tests',right='quality_management'),
    _n('tt.t2_tol','Tolerance',1,5,3,'p.101',unit='°C',integer=True,section='tests',right='quality_management',vis=[('tt.t2','in',(True,))]),
    _b('tt.t3','Temperature 3 (additional)',False,'p.101',section='tests',right='quality_management'),
    _n('tt.t3_temp','Temperature 3',50,200,120,'p.101',unit='°C',integer=True,section='tests',right='quality_management',vis=[('tt.t3','in',(True,))],
       assumption='value shown when switched on'),
    _n('tt.t3_tol','Tolerance',1,5,3,'p.101',unit='°C',integer=True,section='tests',right='quality_management',vis=[('tt.t3','in',(True,))]),
    _e('wa_ext.weight','Test weight (weight adjustment - external)',('Test weight 1','Test weight 2'),'Test weight 1','p.102',section='adjustments',
       right='quality_management'),
    _n('ta.t1','Temperature 1',80,140,100,'p.102',unit='°C',integer=True,section='adjustments',right='quality_management'),
    _n('ta.t2','Temperature 2',140,200,160,'p.102',unit='°C',integer=True,section='adjustments',right='quality_management'),
    _e('qm.print_language','Printout language',LANG9,'English','p.103',section='qm_print',right='quality_management'),
    _b('qm.print_auto','Print results automatically after test/adjustment',False,'p.103',section='qm_print',right='quality_management'),
    _e('sc.lot_input','Lot number input',('OFF','Free input','Guided input'),'Guided input','p.103',section='smartcal',right='quality_management',
       assumption='OFF / Free input (not required) / Guided input (required)'),
    _b('sc.clear_lot','Clear lot number after measurement',False,'p.103',section='smartcal',right='quality_management'),
    _b('sc.csmartcal','Use cSmartCal',False,'p.103',section='smartcal',right='quality_management'),
]+[f for i in range(1,7) for f in (
    [_b(f'sc.m{i}.on',f'SmartCal method {i}'+(f' ({SC_FIXED[i-1]} °C)' if i<=4 else ' (custom)'),True,'p.103',section='smartcal',right='quality_management')]+
    ([_n(f'sc.m{i}.temp',f'Method {i} temperature',40,230,105,'p.103',unit='°C',integer=True,section='smartcal',right='quality_management',
         vis=[(f'sc.m{i}.on','in',(True,))])] if i>4 else [])+
    [_b(f'sc.m{i}.limits',f'Method {i} control limits',i<=4,'p.104',section='smartcal',right='quality_management',vis=[(f'sc.m{i}.on','in',(True,))]),
     _n(f'sc.m{i}.lo',f'Method {i} lower limit',0.0,100.0,SC_LIMITS[SC_FIXED[i-1]][0] if i<=4 else 0.0,'p.104',unit='%MCN',section='smartcal',
        right='quality_management',vis=[(f'sc.m{i}.on','in',(True,)),(f'sc.m{i}.limits','in',(True,))]),
     _n(f'sc.m{i}.hi',f'Method {i} upper limit',0.0,100.0,SC_LIMITS[SC_FIXED[i-1]][1] if i<=4 else 100.0,'p.104',unit='%MCN',section='smartcal',
        right='quality_management',vis=[(f'sc.m{i}.on','in',(True,)),(f'sc.m{i}.limits','in',(True,))])])]+[
    _b('fact','FACT',True,'p.103',section='fact',right='quality_management'),
    _b('qp.enabled','QuickPredict',True,'p.57, p.103',section='qp',right='quality_management'),
    # User management (p.106): right user_management
    _b('policy.startup','Default startup user',True,'p.106',section='policies',right='user_management'),
    _s('policy.startup_user','Startup user',1,30,'Administrator','p.106',section='policies',right='user_management',vis=[('policy.startup','in',(True,))]),
    _b('policy.lockout','Auto lock-out',False,'p.106',section='policies',right='user_management'),
    _n('policy.lockout_min','Minutes',1,99,10,'p.106',unit='min',integer=True,section='policies',right='user_management',vis=[('policy.lockout','in',(True,))],
       assumption='value shown when switched on'),
    # System and data management (p.107): right system_data_management
    _b('results.auto_delete','Auto-delete',True,'p.107',section='results',right='system_data_management'),
    _n('results.limit','Result limit',0,3000,1000,'p.107',integer=True,section='results',right='system_data_management',vis=[('results.auto_delete','in',(True,))]),
]
HX_SETTING={f['key']:f for f in HX_SETTINGS}
HX_USER_FIELDS=[_s('user_name','User name',1,30,'','p.108'),_s('full_name','Full name',0,30,'','p.108'),_s('description','Description',0,60,'','p.108'),
                _b('active','Account active',False,'p.108'),_f('group','Group','group','Operators','p.108')]
HX_GROUP_FIELDS=[_s('group_name','Group name',1,30,'','p.105'),_s('description','Description',0,60,'','p.104')]+[
    _b(k,label,False,'p.104-105') for k,label in HX_RIGHTS]


# ---------------------------------------------------------------- terminal helpers
def _need(ok,code='PRECONDITION_FAILED'):
    if not ok: raise ActionRejected(code)

def _visible(f,values):
    for key,op,want in f.get('vis',[]):
        v=values.get(key)
        if (op=='in' and v not in want) or (op=='not_in' and v in want): return False
    return True

def _check(f,v,options=None):
    """Type / range check of one table value."""
    t=f['type']
    if t=='bool': return type(v) is bool
    if t=='enum': return v in f['options'] and type(v) is type(f['options'][0])
    if t in ('int','num'):
        if v is None: return bool(f.get('nullable'))
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or (t=='int' and float(v)!=int(v)): return False
        return f['min']-1e-12<=v<=f['max']+1e-12
    if t=='text':
        if not isinstance(v,str) or not f['min_len']<=len(v)<=f['max_len']: return False
        if f.get('pattern'):
            import re
            return re.match(f['pattern'],v) is not None
        return True
    if t=='time':
        if v is None: return bool(f.get('nullable'))
        return isinstance(v,str) and _parse_time(v) is not None
    if t=='multi':
        pool=options if options is not None else f.get('options',[])
        return isinstance(v,list) and all(x in pool for x in v) and len(set(v))==len(v)
    if t=='group': return isinstance(v,str)
    return False

def _parse_time(v):
    try:
        h,m=v.split(':'); h,m=int(h),int(m)
        return (h,m) if 0<=h<24 and 0<=m<60 and len(v)==5 else None
    except (ValueError,AttributeError):
        return None

def _defaults(fields): return {f['key']:copy.deepcopy(f['default']) for f in fields}

def _schema(f):
    """JSON schema of one table value (command arguments and state slots)."""
    t=f['type']
    if t=='bool': s=dict(BOOL)
    elif t=='enum':
        s={'type':'integer','enum':list(f['options']),'x-unit':'1'} if isinstance(f['options'][0],int) else {'type':'string','enum':list(f['options'])}
    elif t in ('int','num'): s=dict(number(f.get('unit') or '1',f['min'],f['max']),type='integer' if t=='int' else 'number')
    elif t=='text': s=dict(type='string',minLength=f['min_len'],maxLength=f['max_len'])
    elif t=='time': s=dict(type='string',pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    elif t=='multi': s=dict(type='array',items=TEXT,minItems=0,maxItems=16)
    else: s=dict(TEXT)
    if f.get('nullable'): s['type']=[s['type'],'null']
    return s

def _settings_schema(fields):
    return dict(type='object',properties={f['key']:_schema(f) for f in fields},required=[],additionalProperties=False,minProperties=1)

def _new_method(mid,name):
    p=_defaults(HX_METHOD_FIELDS); p['name']=name; p['steps']=copy.deepcopy(STEP_DEFAULTS)
    return dict(id=mid,name=name,version=1,params=p,qp_refs=[],qp_knowledge=None)

def _new_user(name,group,active=True,prefs=None):
    return dict(user_name=name,full_name='',description='',active=active,password=None,group=group,prefs=prefs or _defaults(HX_PREF_FIELDS),shortcuts=[],
                methods=None,modified=None)

def _default_prefs(dev):
    """Preferences of a new user and of the terminal without a logged-in user: Instrument settings > Default settings (p.90-91)."""
    p=_defaults(HX_PREF_FIELDS); s=dev['settings']
    p.update(language=s['default.language'],keyboard=s['default.keyboard'],brightness=s['default.brightness'],scheme=s['default.scheme'])
    return p

def _new_device():
    groups=[dict(group_name=g,description='',builtin=True,rights={k:k in r for k in RIGHT_KEYS},modified=None) for g,r in GROUP_DEFAULTS.items()]
    return dict(settings=_defaults(HX_SETTINGS),users=[_new_user('Administrator','Administrators')],groups=groups,current_user='Administrator',
                setup_done=True,clock_offset_s=0.0,methods=[],next_method=1,draft=None,work=None,results=[],next_result=1,ids=[],
                history=dict(weight_test=[],weight_adjustment=[],temperature_test=[],temperature_adjustment=[],smartcal=[]),proc=None,last_proc=None,
                power_epoch=0,last_fact_s=None,last_activity_s=0.0,test_result=None,wizard=None,qp_wizard=None,assistant=None)

def _fmt_dec(v,n):
    x=math.floor(abs(v)*10**n+.5+1e-9)/10**n
    return f'{math.copysign(x,v) if x else 0.0:.{n}f}'

def _round_to(v,step): return round(round(v/step)*step,9)


# ---------------------------------------------------------------- terminal commands: argument schemas, effects and implementations
TERMINAL={}

def term(name,inp,out,components,meaning,effects,example=None,kind='device_command',visibility='agent'):
    def deco(fn):
        TERMINAL[name]=fn
        CAPS[name]=(inp,out,components,meaning,kind,visibility,example if example is not None else {},list(effects))
        return fn
    return deco

NAME30=dict(type='string',minLength=1,maxLength=30)
MID=dict(type='integer',minimum=1,maximum=1000000,**{'x-unit':'1'})
OK_OUT=obj({'ok':BOOL})
SCREEN=['screen','terminal']


def _rt_dev(rt):
    return rt.dev


def _user(dev,name=None):
    name=dev['current_user'] if name is None else name
    return next((u for u in dev['users'] if u['user_name']==name),None)


def _rights(dev):
    u=_user(dev)
    if u is None: return set()
    g=next((g for g in dev['groups'] if g['group_name']==u['group']),None)
    return {k for k,v in (g['rights'] if g else {}).items() if v}


def _gate(rt,right=None,login=True,idle=False):
    """Terminal commands: the terminal is on, a user is logged in (when login), the user's group has the right, no measurement runs (idle)."""
    _need(rt.powered,'STANDBY')
    _need(rt.dev['setup_done'] or right=='setup','SETUP_REQUIRED')
    if login: _need(rt.dev['current_user'] is not None,'LOGIN_REQUIRED')
    if right and right!='setup': _need(right in _rights(rt.dev),'PERMISSION_DENIED')
    if idle: _need(rt.phase!='running' and rt.dev['proc'] is None,'BUSY')


def _now_dt(rt):
    """Local time shown by the terminal: the instrument clock runs in UTC and the Regional settings time zone (p.88) gives the local time."""
    utc=CLOCK0_UTC+datetime.timedelta(seconds=rt.time_s+rt.dev['clock_offset_s'])
    return utc.astimezone(_zone(rt.dev['settings']['region.time_zone'])).replace(tzinfo=None)


@term('settings.configure',obj({'settings':_settings_schema(HX_SETTINGS)}),obj({'changed':dict(type='array',items=TEXT)}),SCREEN,
      '设置（日期时间格式、外设、网络、默认、共享目录、公司信息、标识、打印导出、测试/调整设置、FACT、QuickPredict、账户策略、结果管理）：按手册设置表校验后写入',
      ['device.settings'],{'settings':{'region.date_format':'YYYY-MM-DD'}})
def _c_settings(rt,a):
    dev=rt.dev; s=dev['settings']; new=dict(s)
    for k,v in a['settings'].items():
        f=HX_SETTING.get(k); _need(f is not None,'INVALID_ARGUMENT')
        _gate(rt,f['right'])
        pool=list(dev['ids']) if f.get('options_from')=='ids' else None
        _need(_check(f,v,pool),'INVALID_ARGUMENT'); new[k]=copy.deepcopy(v)
    _need(new['ta.t2']-new['ta.t1']>=60,'TEMPERATURE_SPAN')                          # p.102
    if new['policy.startup']: _need(_user(dev,new['policy.startup_user']) is not None,'NOT_FOUND')
    if new['sc.csmartcal']!=s['sc.csmartcal']:                                        # p.103: cSmartCal sets the recommended limits
        lim=SC_CLIMITS if new['sc.csmartcal'] else SC_LIMITS
        for i,t in enumerate(SC_FIXED,1):                                             # (limits edited in the same entry are kept)
            if new[f'sc.m{i}.lo']==s[f'sc.m{i}.lo']: new[f'sc.m{i}.lo']=lim[t][0]
            if new[f'sc.m{i}.hi']==s[f'sc.m{i}.hi']: new[f'sc.m{i}.hi']=lim[t][1]
    for i in (5,6):                                                                   # p.104: a custom temperature resets its limits (off)
        if new[f'sc.m{i}.temp']!=s[f'sc.m{i}.temp'] and new[f'sc.m{i}.limits']==s[f'sc.m{i}.limits']: new[f'sc.m{i}.limits']=False
    for i in range(1,7): _need(new[f'sc.m{i}.lo']<=new[f'sc.m{i}.hi'],'INVALID_ARGUMENT')
    if s['qp.enabled']!=new['qp.enabled'] and not new['qp.enabled']:
        for m in dev['methods']: m['params']['qp_usage']='Inactive'                   # p.57: disabling applies to all methods
    changed=sorted(k for k in new if new[k]!=s[k]); dev['settings']=new
    if 'results.limit' in changed or 'results.auto_delete' in changed: _apply_auto_delete(dev)
    return {'changed':changed}


@term('preferences.configure',obj({'preferences':_settings_schema(HX_PREF_FIELDS)}),obj({'changed':dict(type='array',items=TEXT)}),SCREEN,
      '当前用户的用户偏好（显示语言、键盘语言、亮度、配色、声音、音量）',['users.state'],{'preferences':{'brightness':60}})
def _c_prefs(rt,a):
    _gate(rt,'user_preferences')
    u=_user(rt.dev); new=dict(u['prefs'])
    for k,v in a['preferences'].items():
        f=next(x for x in HX_PREF_FIELDS if x['key']==k); _need(_check(f,v),'INVALID_ARGUMENT'); new[k]=v
    changed=sorted(k for k in new if new[k]!=u['prefs'][k]); u['prefs']=new
    return {'changed':changed}


@term('clock.set',obj({'date':dict(type='string',pattern=r'^\d{4}-\d{2}-\d{2}$'),'time':dict(type='string',pattern=r'^\d{2}:\d{2}$')},optional=('date','time')),
      obj({'datetime':TEXT}),SCREEN,'设置日期/时间（Regional Settings，Set date / Set time）',['device.clock'],{'date':'2026-10-18','time':'09:30'})
def _c_clock(rt,a):
    _gate(rt,'instrument_settings')
    now=_now_dt(rt); d,t=now.date(),now.time()
    if 'date' in a:
        try: d=datetime.date.fromisoformat(a['date'])
        except ValueError: raise ActionRejected('INVALID_ARGUMENT') from None
    if 'time' in a:
        hm=_parse_time(a['time']); _need(hm is not None,'INVALID_ARGUMENT'); t=datetime.time(hm[0],hm[1],0)
    target=datetime.datetime.combine(d,t).replace(tzinfo=_zone(rt.dev['settings']['region.time_zone']))   # entered as local time of the zone
    rt.dev['clock_offset_s']=(target.astimezone(datetime.timezone.utc)-CLOCK0_UTC).total_seconds()-rt.time_s
    return {'datetime':_now_dt(rt).isoformat(timespec='seconds')}


# ---- methods (p.33-53): drafts are edited and stored with [Save]
def _method(dev,mid): return next((m for m in dev['methods'] if m['id']==mid),None)

def _check_params(p):
    """All method parameters valid (ranges, ATRO limit range, unique steps, total time)."""
    for f in HX_METHOD_FIELDS:
        _need(_check(f,p.get(f['key'])),'INVALID_ARGUMENT')
    hi=1000.0 if p['display_mode'] in ATRO else 100.0
    for k in ('t1_plus','t1_minus','t2_plus','t2_minus'):
        _need(p[k] is None or p[k]<=hi,'INVALID_ARGUMENT')
    _need(isinstance(p.get('steps'),list) and 1<=len(p['steps'])<=5,'INVALID_ARGUMENT')
    for st in p['steps']:
        _need(isinstance(st,dict) and set(st)=={f['key'] for f in STEP_FIELDS},'INVALID_ARGUMENT')
        for f in STEP_FIELDS: _need(_check(f,st[f['key']]),'INVALID_ARGUMENT')

def _qp_issues(p):
    """Settings incompatible with QuickPredict (p.55-56)."""
    out=[]
    if p['program'] not in DRY: out.append('Drying program')
    if p['display_mode'] not in ('%MC','%DC','g','-%MC'): out.append('Display mode')
    if not p['start_weight']: out.append('Start weight')
    if p['weigh_in_aid']!='Active': out.append('Weigh-in aid')
    return out


@term('method.create',obj({'name':NAME30}),obj({'draft':BOOL}),SCREEN,'Method Definition > New... > Define method manually：以出厂参数新建方法草稿（Save后存储，最多300个，名称唯一）',
      ['method.draft'],{'name':'My first method'})
def _c_method_create(rt,a):
    _gate(rt,'method_definition',idle=True); dev=rt.dev
    _need(len(dev['methods'])<MAX_METHODS,'MEMORY_FULL')
    _need(not any(m['name']==a['name'] for m in dev['methods']),'NAME_EXISTS')
    dev['draft']=dict(_new_method(None,a['name']),base_version=None)
    return {'draft':True}


@term('method.edit',obj({'method_id':MID}),obj({'draft':BOOL}),SCREEN,'打开已存方法的参数菜单（草稿副本，Save后生成新版本）',['method.draft'],{'method_id':1})
def _c_method_edit(rt,a):
    _gate(rt,'method_definition',idle=True); m=_method(rt.dev,a['method_id']); _need(m is not None,'NOT_FOUND')
    rt.dev['draft']=dict(copy.deepcopy(m),base_version=m['version'])
    return {'draft':True}


def _draft(rt):
    _gate(rt,'method_definition',idle=True); d=rt.dev['draft']; _need(d is not None,'NO_DRAFT'); return d


@term('method.draft_set',obj({'values':dict(type='object',additionalProperties=True,minProperties=1)}),obj({'changed':dict(type='array',items=TEXT)}),SCREEN,
      '修改方法草稿参数（干燥程序、温度、关断标准、显示模式、起始重量、控制限、分辨率、自由因子、QuickPredict、工作流、名称、样品制备）',['method.draft'],
      {'values':{'temperature':120}})
def _c_draft_set(rt,a):
    d=_draft(rt); p=dict(d['params']); dev=rt.dev
    for k,v in a['values'].items():
        f=HX_METHOD_FIELD.get(k); _need(f is not None,'INVALID_ARGUMENT'); _need(_check(f,v),'INVALID_ARGUMENT'); p[k]=copy.deepcopy(v)
    if p['display_mode'] in ATRO: p['resolution']='Standard'                          # p.46: no high resolution in ATRO modes
    if p['display_mode']=='g': p['free_factor']=False                                  # p.47
    if p['qp']:
        _need(dev['settings']['qp.enabled'],'QP_DISABLED')
        issues=_qp_issues(p)
        if issues: raise ActionRejected('QP_INCOMPATIBLE')
    if p['qp_usage']=='Active': _need(p['qp'] and d['qp_knowledge'] is not None,'QP_NO_KNOWLEDGE')
    if 'name' in a['values']:
        _need(not any(m['name']==p['name'] and m['id']!=d['id'] for m in dev['methods']),'NAME_EXISTS')
    _check_params(p)
    changed=sorted(k for k in p if p[k]!=d['params'][k]); d['params']=p; d['name']=p['name']
    return {'changed':changed}


STEP_SCHEMA=dict(type='object',additionalProperties=True)
@term('method.draft_steps',obj({'op':{'type':'string','enum':['add','remove','set']},'index':dict(type='integer',minimum=0,maximum=4,**{'x-unit':'1'}),
                                'values':STEP_SCHEMA},optional=('index','values')),obj({'steps':dict(type='integer',minimum=1,maximum=5,**{'x-unit':'1'})}),SCREEN,
      '分段干燥（Steps）：增加/删除/修改干燥段（1-5段，每段温度与关断标准）',['method.draft'],{'op':'set','index':0,'values':{'temperature':60}})
def _c_draft_steps(rt,a):
    d=_draft(rt); steps=copy.deepcopy(d['params']['steps'])
    if a['op']=='add':
        _need(len(steps)<5,'INVALID_ARGUMENT'); steps.append(copy.deepcopy(steps[-1]))
    elif a['op']=='remove':
        i=a.get('index',len(steps)-1); _need(len(steps)>1 and i<len(steps),'INVALID_ARGUMENT'); steps.pop(i)
    else:
        i=a.get('index',0); _need(i<len(steps) and isinstance(a.get('values'),dict),'INVALID_ARGUMENT')
        for k,v in a['values'].items():
            f=next((x for x in STEP_FIELDS if x['key']==k),None); _need(f is not None and _check(f,v),'INVALID_ARGUMENT'); steps[i][k]=v
    d['params']['steps']=steps
    return {'steps':len(steps)}


def _store_draft(rt,name=None):
    dev=rt.dev; d=dev['draft']; p=copy.deepcopy(d['params'])
    if name is not None:
        p['name']=name
    _need(not any(m['name']==p['name'] and (name is not None or m['id']!=d['id']) for m in dev['methods']),'NAME_EXISTS')
    _check_params(p)
    if name is not None or d['id'] is None:
        _need(len(dev['methods'])<MAX_METHODS,'MEMORY_FULL')
        m=dict(id=dev['next_method'],name=p['name'],version=1,params=p,qp_refs=copy.deepcopy(d['qp_refs']) if name is None else [],
               qp_knowledge=copy.deepcopy(d['qp_knowledge']) if name is None else None)
        dev['next_method']+=1; dev['methods'].append(m)
    else:
        m=_method(dev,d['id']); _need(m is not None,'NOT_FOUND')
        if any(m['params'].get(k)!=p.get(k) for k in RESULT_KEYS) and d.get('qp_recorded_with')!={k:p.get(k) for k in RESULT_KEYS}:
            # p.55: a result-influencing change deletes the reference data (not the curves the QuickPredict Wizard just recorded with these settings)
            d['qp_refs'],d['qp_knowledge']=[],None
            if p['qp_usage']=='Active': p['qp_usage']='Inactive'
        if p!=m['params'] or d['qp_refs']!=m['qp_refs']: m['version']+=1             # p.72: every change makes a new version
        m.update(name=p['name'],params=p,qp_refs=copy.deepcopy(d['qp_refs']),qp_knowledge=copy.deepcopy(d['qp_knowledge']))
    dev['draft']=None
    return m


@term('method.save',obj({}),obj({'method_id':MID,'version':dict(type='integer',minimum=1,**{'x-unit':'1'})}),SCREEN,'[Save]：存储方法草稿（新方法或新版本）',
      ['method.draft','methods.list'])
def _c_method_save(rt,a):
    _draft(rt); m=_store_draft(rt)
    return {'method_id':m['id'],'version':m['version']}


@term('method.save_as',obj({'name':NAME30}),obj({'method_id':MID,'version':dict(type='integer',minimum=1,**{'x-unit':'1'})}),SCREEN,
      '[Save as...]：把草稿以新名称存为新方法（复制方法）',['method.draft','methods.list'],{'name':'Copy of method'})
def _c_method_save_as(rt,a):
    _draft(rt); m=_store_draft(rt,a['name'])
    return {'method_id':m['id'],'version':m['version']}


@term('method.discard',obj({}),OK_OUT,SCREEN,'离开参数菜单不存储（草稿丢弃；Method Wizard随之结束）',['method.draft','wizard.state'])
def _c_method_discard(rt,a):
    _gate(rt,'method_definition'); _need(rt.dev['qp_wizard'] is None,'BUSY'); rt.dev['draft']=None; rt.dev['wizard']=None
    return {'ok':True}


@term('method.delete',obj({'method_id':MID}),OK_OUT,SCREEN,'[Delete]：删除方法及其全部结果与快捷方式（p.51）',['methods.list','results.list','users.state','method.draft'],
      {'method_id':1})
def _c_method_delete(rt,a):
    _gate(rt,'method_definition',idle=True); dev=rt.dev
    _need(_method(dev,a['method_id']) is not None,'NOT_FOUND')
    _need(not dev['work'] or dev['work']['method_id']!=a['method_id'],'IN_USE')
    dev['methods']=[m for m in dev['methods'] if m['id']!=a['method_id']]
    dev['results']=[r for r in dev['results'] if r['method_id']!=a['method_id']]
    for u in dev['users']:
        u['shortcuts']=[s for s in u['shortcuts'] if s['method_id']!=a['method_id']]
        if u['methods'] is not None: u['methods']=[x for x in u['methods'] if x!=a['method_id']]
    if dev['draft'] and dev['draft']['id']==a['method_id']: dev['draft']=None
    return {'ok':True}


@term('method.automet',obj({'reference':number('1',-1000,1000)}),obj({'proposal':{'type':['object','null']},'message':TEXT}),SCREEN,
      'AutoMet（p.54）：用最近一次测试测量曲线与参考值求自由关断标准Free (mg / s)（限1 mg/20 s–1 mg/140 s）并写入方法草稿',['method.draft'],{'reference':10.0})
def _c_automet(rt,a):
    d=_draft(rt); dev=rt.dev; tr=dev['test_result']
    _need(tr is not None and tr['method_id']==d['id'],'NO_TEST_MEASUREMENT')
    p=d['params']; ww=tr['start_g']; target=a['reference']; curve=tr['curve']
    hit=next((pt for pt in curve if _value(p['display_mode'],ww,pt[1]*1000.0)>=target-1e-9) if p['display_mode']!='%DC' else
             (pt for pt in curve if _value(p['display_mode'],ww,pt[1]*1000.0)<=target+1e-9),None)
    if hit is None: return {'proposal':None,'message':'Target value not reached: increase the drying temperature.'}
    t=hit[0]; window=50.0
    m0=_interp(curve,max(0.0,t-window)); m1=hit[1]
    loss_mg=max((m0-m1)*1e6,1e-9)*window/max(min(window,t),1e-9)
    dt_per_mg=window/loss_mg
    if dt_per_mg<20: return {'proposal':None,'message':'Free (mg / s) below 1 mg / 20 s: reduce the drying temperature.'}
    if dt_per_mg>140: return {'proposal':None,'message':'Free (mg / s) above 1 mg / 140 s: use switch-off criterion 5 or increase the drying temperature.'}
    dt=int(round(dt_per_mg)); prop=dict(soc='Free (mg / s)',soc_free_mg=1.0,soc_free_mg_dt=dt)
    d['params'].update(prop)
    return {'proposal':prop,'message':f'Proposed switch-off criterion: Free (mg / s) 1 mg / {dt} s'}


# ---- shortcuts, method selection, identifications (p.68-69, p.94, p.98)
MAX_SHORTCUTS=12
@term('shortcut.add',obj({'method_id':MID,'name':NAME30}),OK_OUT,SCREEN,'当前用户首页快捷方式：新增或改名（工作屏标题栏图标或Edit shortcuts...，p.68-69）',['users.state'],
      {'method_id':1,'name':'My method'})
def _c_shortcut_add(rt,a):
    _gate(rt,'edit_shortcuts'); dev=rt.dev; _need(_method(dev,a['method_id']) is not None,'NOT_FOUND')
    sc=[x for x in _user(dev)['shortcuts'] if x['method_id']!=a['method_id']]
    _need(len(sc)<MAX_SHORTCUTS,'MEMORY_FULL')
    _user(dev)['shortcuts']=sc+[dict(method_id=a['method_id'],name=a['name'])] if len(sc)==len(_user(dev)['shortcuts']) else \
        [dict(x,name=a['name']) if x['method_id']==a['method_id'] else x for x in _user(dev)['shortcuts']]
    return {'ok':True}


@term('shortcut.remove',obj({'method_id':MID}),OK_OUT,SCREEN,'删除当前用户的一个首页快捷方式（Edit shortcuts...中取消勾选）',['users.state'],{'method_id':1})
def _c_shortcut_remove(rt,a):
    _gate(rt,'edit_shortcuts'); u=_user(rt.dev); _need(any(x['method_id']==a['method_id'] for x in u['shortcuts']),'NOT_FOUND')
    u['shortcuts']=[x for x in u['shortcuts'] if x['method_id']!=a['method_id']]
    return {'ok':True}


@term('user.select_methods',obj({'user_name':NAME30,'method_ids':{'type':['array','null'],'items':MID,'maxItems':MAX_METHODS}}),OK_OUT,SCREEN,
      'Method selection：为某用户启用/停用方法（null为全部方法）',['users.state'],{'user_name':'Administrator','method_ids':None})
def _c_select_methods(rt,a):
    _gate(rt,'select_methods'); dev=rt.dev; u=_user(dev,a['user_name']); _need(u is not None,'NOT_FOUND')
    ids=a['method_ids']
    if ids is not None:
        _need(all(_method(dev,i) is not None for i in ids) and len(set(ids))==len(ids),'INVALID_ARGUMENT')
    u['methods']=None if ids is None else list(ids)
    return {'ok':True}


@term('identification.add',obj({'name':dict(type='string',minLength=1,maxLength=20)}),OK_OUT,SCREEN,'新增标识（最多10个，1-20字符）',['identifications'],{'name':'Batch'})
def _c_id_add(rt,a):
    _gate(rt,'application_settings'); dev=rt.dev
    _need(len(dev['ids'])<MAX_IDS,'MEMORY_FULL'); _need(a['name'] not in dev['ids'],'NAME_EXISTS')
    dev['ids'].append(a['name'])
    return {'ok':True}


@term('identification.remove',obj({'name':dict(type='string',minLength=1,maxLength=20)}),OK_OUT,SCREEN,'删除标识',['identifications','device.settings'],{'name':'Batch'})
def _c_id_remove(rt,a):
    _gate(rt,'application_settings'); dev=rt.dev; _need(a['name'] in dev['ids'],'NOT_FOUND')
    dev['ids'].remove(a['name']); dev['settings']['ids.clear']=[x for x in dev['settings']['ids.clear'] if x!=a['name']]
    if dev['work']: dev['work']['ids'].pop(a['name'],None)
    return {'ok':True}


# ---- users, groups, login (p.104-106)
@term('session.login',obj({'user_name':NAME30,'password':dict(type='string',maxLength=30)},optional=('password',)),obj({'user_name':TEXT}),SCREEN,
      '登录（启动或注销后的登录对话框；密码保护的账户需密码）',['users.state'],{'user_name':'Administrator'})
def _c_login(rt,a):
    _need(rt.powered,'STANDBY'); dev=rt.dev
    u=_user(dev,a['user_name']); _need(u is not None and u['active'],'WRONG_PASSWORD')
    _need(u['password'] is None or a.get('password')==u['password'],'WRONG_PASSWORD')
    dev['current_user']=u['user_name']; dev['locked']=False
    return {'user_name':u['user_name']}


@term('session.logout',obj({}),obj({'user_name':{'type':['string','null']}}),SCREEN,'注销：非默认用户注销后自动激活默认启动用户（p.106），否则回到登录对话框',
      ['users.state'])
def _c_logout(rt,a):
    _gate(rt,idle=False); dev=rt.dev; s=dev['settings']
    start=s['policy.startup_user'] if s['policy.startup'] else None
    dev['current_user']=start if start and start!=dev['current_user'] and _user(dev,start) and _user(dev,start)['active'] else None
    return {'user_name':dev['current_user']}


USER_VALUES=_settings_schema(HX_USER_FIELDS)
@term('user.save',obj({'user_name':NAME30,'values':USER_VALUES,'create':BOOL}),OK_OUT,SCREEN,'新建或编辑用户账户（全名、描述、账户激活、组；最多30个）',['users.state'],
      {'user_name':'Operator 1','values':{'user_name':'Operator 1','active':True,'group':'Operators'},'create':True})
def _c_user_save(rt,a):
    _gate(rt,'user_management'); dev=rt.dev
    v=a['values']
    for k,x in v.items():
        f=next(f for f in HX_USER_FIELDS if f['key']==k); _need(_check(f,x),'INVALID_ARGUMENT')
    if 'group' in v: _need(any(g['group_name']==v['group'] for g in dev['groups']),'NOT_FOUND')
    if a['create']:
        _need(_user(dev,a['user_name']) is None,'NAME_EXISTS'); _need(len(dev['users'])<MAX_USERS,'MEMORY_FULL')
        _need(v.get('user_name',a['user_name'])==a['user_name'],'INVALID_ARGUMENT')
        u=_new_user(a['user_name'],v.get('group','Operators'),active=v.get('active',False),prefs=_default_prefs(dev)); dev['users'].append(u)
    else:
        u=_user(dev,a['user_name']); _need(u is not None,'NOT_FOUND')
        _need(v.get('user_name',u['user_name'])==u['user_name'],'INVALID_ARGUMENT')
        if u['user_name']=='Administrator': _need(v.get('group','Administrators')=='Administrators' and v.get('active',True),'IN_USE')
    for k,x in v.items():
        if k!='user_name': u[k]=x
    u['modified']=_now_dt(rt).isoformat(timespec='seconds')
    return {'ok':True}


@term('user.delete',obj({'user_name':NAME30}),OK_OUT,SCREEN,'删除用户（不能删除Administrator或当前登录账户）',['users.state'],{'user_name':'Operator 1'})
def _c_user_delete(rt,a):
    _gate(rt,'user_management'); dev=rt.dev; u=_user(dev,a['user_name']); _need(u is not None,'NOT_FOUND')
    _need(u['user_name']!='Administrator' and u['user_name']!=dev['current_user'],'IN_USE')
    dev['users']=[x for x in dev['users'] if x is not u]
    if dev['settings']['policy.startup_user']==a['user_name']: dev['settings']['policy.startup']=False
    return {'ok':True}


@term('user.set_password',obj({'user_name':NAME30,'password':{'type':['string','null'],'maxLength':30}}),OK_OUT,SCREEN,
      '设置/关闭用户密码（管理员在用户属性中；用户在User preferences > Password中改自己的）',['users.state'],{'user_name':'Administrator','password':'secret'})
def _c_user_password(rt,a):
    dev=rt.dev; _gate(rt)
    u=_user(dev,a['user_name']); _need(u is not None,'NOT_FOUND')
    if 'user_management' not in _rights(dev):                                         # p.87: users change their own password if one is set
        _need(a['user_name']==dev['current_user'] and 'user_preferences' in _rights(dev),'PERMISSION_DENIED')
        _need(u['password'] is not None and a['password'] is not None,'PERMISSION_DENIED')
    _need(a['password'] is None or len(a['password'])>=1,'INVALID_ARGUMENT')
    u['password']=a['password']
    return {'ok':True}


GROUP_VALUES=_settings_schema(HX_GROUP_FIELDS)
@term('group.save',obj({'group_name':NAME30,'values':GROUP_VALUES,'create':BOOL}),OK_OUT,SCREEN,'新建或编辑用户组（描述与访问权限；最多10组；Administrators权限不可改）',
      ['users.state'],{'group_name':'Lab','values':{'group_name':'Lab','run_tests':True},'create':True})
def _c_group_save(rt,a):
    _gate(rt,'user_management'); dev=rt.dev; v=a['values']
    for k,x in v.items():
        f=next(f for f in HX_GROUP_FIELDS if f['key']==k); _need(_check(f,x),'INVALID_ARGUMENT')
    if a['create']:
        _need(not any(g['group_name']==a['group_name'] for g in dev['groups']),'NAME_EXISTS'); _need(len(dev['groups'])<MAX_GROUPS,'MEMORY_FULL')
        g=dict(group_name=a['group_name'],description='',builtin=False,rights={k:False for k in RIGHT_KEYS},modified=None); dev['groups'].append(g)
    else:
        g=next((g for g in dev['groups'] if g['group_name']==a['group_name']),None); _need(g is not None,'NOT_FOUND')
    _need(v.get('group_name',a['group_name'])==a['group_name'],'INVALID_ARGUMENT')
    if g['group_name']=='Administrators': _need(all(v.get(k,True) for k in RIGHT_KEYS),'IN_USE')
    if 'description' in v: g['description']=v['description']
    for k in RIGHT_KEYS:
        if k in v: g['rights'][k]=v[k]
    g['modified']=_now_dt(rt).isoformat(timespec='seconds')
    return {'ok':True}


@term('group.delete',obj({'group_name':NAME30}),OK_OUT,SCREEN,'删除用户组（Administrators与有成员的组不能删除）',['users.state'],{'group_name':'Lab'})
def _c_group_delete(rt,a):
    _gate(rt,'user_management'); dev=rt.dev
    g=next((g for g in dev['groups'] if g['group_name']==a['group_name']),None); _need(g is not None,'NOT_FOUND')
    _need(g['group_name']!='Administrators' and not any(u['group']==g['group_name'] for u in dev['users']),'IN_USE')
    dev['groups']=[x for x in dev['groups'] if x is not g]
    return {'ok':True}


# ---- measurement work flow (p.29-32, p.61-68)
def _allowed_methods(dev):
    u=_user(dev)
    if u is None: return []
    return [m for m in dev['methods'] if u['methods'] is None or m['id'] in u['methods']]


@term('measurement.open',obj({'method_id':MID,'test':BOOL,'test_time_s':{'type':['number','null'],'minimum':30,'maximum':28800,'x-unit':'s'}},
                              optional=('test','test_time_s')),obj({'stage':TEXT}),SCREEN+['lid'],
      'Measurement > 方法：打开工作屏（样品室自动打开）；test=True为方法测试测量（Test method > Test measurement，可设测试时间）',['work.state','lid.position_m'],
      {'method_id':1})
def _c_meas_open(rt,a):
    _gate(rt,idle=True); dev=rt.dev
    m=_method(dev,a['method_id']); _need(m is not None,'NOT_FOUND')
    if a.get('test'): _gate(rt,'method_definition')
    else: _need(m in _allowed_methods(dev),'PERMISSION_DENIED')
    if a.get('test') and dev['draft'] is not None: _need(dev['draft']['id']==m['id'],'BUSY')
    dev['work']=dict(method_id=m['id'],test=bool(a.get('test')),test_time_s=a.get('test_time_s'),stage='tare',note='',result_id=None,
                     ids={k:dev['work']['ids'].get(k,'') if dev['work'] else '' for k in dev['ids']},preheat=None,qp_saved=None,armed=False)
    rt.lid_m=.1085                                                                  # the sample chamber opens automatically (p.30, p.61)
    if m['params']['preheating']=='Standby': _preheat_on(rt,m)
    return {'stage':'tare'}


def _work_method(rt,w):
    """The method a work screen runs: a stored method, the open draft (Method Wizard, temperature assistant, QuickPredict Wizard) or a SmartCal
    test method."""
    if w is None: return None
    if w.get('purpose')=='smartcal': return _smartcal_method(rt,w['smartcal']['test'])
    if w.get('draft'):
        d=rt.dev['draft']
        return None if d is None else dict(id=d['id'],name=d['params']['name'],version=d.get('version') or 1,params=d['params'])
    return _method(rt.dev,w['method_id'])


def _open_work(rt,method_id,purpose=None,test=False,test_time_s=None,draft=False,**extra):
    dev=rt.dev
    dev['work']=dict(method_id=method_id,test=test,test_time_s=test_time_s,stage='tare',note='',result_id=None,
                     ids={k:dev['work']['ids'].get(k,'') if dev['work'] else '' for k in dev['ids']},preheat=None,qp_saved=None,armed=False,
                     purpose=purpose,draft=draft,**extra)
    rt.lid_m=.1085                                                                  # the sample chamber opens (p.30, p.61)
    return dev['work']


def _work(rt,stage=None):
    w=rt.dev['work']; _need(w is not None,'NO_METHOD')
    if stage: _need(w['stage'] in stage,'PRECONDITION_FAILED')
    return w


@term('measurement.close',obj({}),OK_OUT,SCREEN,'离开工作屏（Back；运行中不能离开）；QuickPredict Wizard的参考测量工作屏离开即中止向导（参考曲线不保存）',
      ['work.state','qp_wizard.state','method.draft'])
def _c_meas_close(rt,a):
    _gate(rt,idle=True); w=rt.dev['work']
    if w is not None and w.get('purpose')=='qp_ref' and rt.dev['qp_wizard'] is not None: _qp_wizard_abort(rt)
    rt.dev['work']=None; rt.dev['preheat']=None
    return {'ok':True}


def _gross_kg(rt): return rt.pan_load_kg+rt.residue_kg+sum(rt.remaining.values())+(rt.smartcal['mass_kg']-rt.smartcal['released_kg'] if rt.smartcal else 0.)

def _corrected_kg(rt):
    e=rt.sensor
    return ((_gross_kg(rt))*(1+e['span_rel'])+e['zero_kg'])*rt.cal['span']


# ---- error messages of manual 9.1 (1.3.0 sweep): causes only an evaluator can create; the display replaces the weight, tare / start refused
HX_FAULTS={'no_sensor':('NO_SENSOR','No sensor connected.'),'overload':('OVERLOAD','Overload'),'underload':('UNDERLOAD','Underload'),
           'zero_range':('ZERO_RANGE','0.0000')}


def _weigh_fault(rt):
    """The first present cause that stops weighing (the order of the manual table after the missing sensor)."""
    return next((c for c in HX_FAULTS if c in getattr(rt,'hx_faults',())),None)


def _need_weighing(rt):
    f=_weigh_fault(rt)
    _need(f is None,HX_FAULTS[f][0] if f else 'PRECONDITION_FAILED')


@term('fault.inject',obj({'code':{'type':'string','enum':list(HX_FAULTS)}}),obj({'injected':dict(type='array',items=TEXT,maxItems=4)}),['balance_sensor','screen'],
      '评测者：注入手册9.1的错误：no_sensor（干燥单元与终端通信失败：No sensor connected.，正在进行的干燥中止）、overload（秤盘上的重量超过量程：Overload）、'
      'underload（样品盘架缺失：Underload）、zero_range（开机或置零时超出零点范围：零闪烁）；工作屏以该信息代替重量，去皮与开始被拒',
      ['fault.injected','measurement.live','cycle.phase','work.state','results.list'],{'code':'underload'},kind='simulation',visibility='evaluator')
def _c_fault_inject(rt,a):
    if a['code'] not in rt.hx_faults: rt.hx_faults.append(a['code'])
    if a['code']=='no_sensor' and rt.phase=='running':                               # no weight values from the drying unit: the drying ends
        rt.phase='cancelled'
        if rt.run is not None: _finish_run(rt,aborted=True,save=True)
    return {'injected':list(rt.hx_faults)}


@term('fault.clear',obj({'code':{'type':'string','enum':list(HX_FAULTS)}}),obj({'injected':dict(type='array',items=TEXT,maxItems=4)}),['balance_sensor','screen'],
      '评测者：手册9.1的措施已完成（减轻样品、装上盘架、取下重物后重试、连接电缆）：消除错误原因',['fault.injected','measurement.live'],{'code':'underload'},
      kind='simulation',visibility='evaluator')
def _c_fault_clear(rt,a):
    if a['code'] in rt.hx_faults: rt.hx_faults.remove(a['code'])
    return {'injected':list(rt.hx_faults)}


@term('measurement.tare',obj({}),obj({'tare_kg':number('kg')}),SCREEN+['lid','balance_sensor'],'[->0/T<-]：样品室自动关闭去皮、完成后再打开（p.30, p.62）',
      ['balance.tare_kg','balance.indicated_kg','work.state','lid.position_m'])
def _c_meas_tare(rt,a):
    _gate(rt,idle=True); w=_work(rt,('tare','sample','done'))
    _need(rt.phase!='running','BUSY')
    _need_weighing(rt)
    rt.tare_kg=_corrected_kg(rt); rt.lid_m=.1085; w['stage']='sample'
    return {'tare_kg':rt.tare_kg}


def _net_kg(rt): return _corrected_kg(rt)-rt.tare_kg

def _read_g(rt,resolution='Standard'):
    return _round_to(_net_kg(rt)*1000.0,0.0001 if resolution=='High' else 0.001)


def _start_check(rt,m,w):
    _need_weighing(rt)
    p=m['params']; net_g=_net_kg(rt)*1000.0
    _need(net_g>=0.1-1e-9,'SAMPLE_TOO_LIGHT')                                      # minimum sample weight 0.1 g (p.125)
    if p['start_weight'] and p['weigh_in_aid']=='Active':                             # p.44, p.64: active aid blocks out-of-tolerance weights
        lo,hi=p['start_weight_g']*(1-p['start_weight_tol']/100.),p['start_weight_g']*(1+p['start_weight_tol']/100.)
        _need(lo-1e-9<=net_g<=hi+1e-9,'START_WEIGHT_TOLERANCE')
    s=rt.dev['settings']
    if s['results.auto_delete'] is False: _need(len(rt.dev['results'])<3000,'RESULT_MEMORY_FULL')


@term('measurement.start',obj({}),obj({'phase':PHASE,'operation_id':{'type':['string','null']}}),SCREEN+['lid','heater','sample_pan','balance_sensor'],
      '[Start drying]：自动模式关闭样品室并开始干燥；手动模式在开着的样品室记录起始重量，按开/关键关闭后开始（p.46, p.62）',
      ['cycle.phase','cycle.elapsed_s','work.state','lid.position_m','method.mode','method.duration_s','method.basis','thermal.target_k','qp_wizard.state',
       'smartcal.state'])
def _c_meas_start(rt,a):
    _gate(rt,idle=True); w=_work(rt,('sample',)); dev=rt.dev
    m=_work_method(rt,w); _need(m is not None,'NO_DRAFT'); purpose=w.get('purpose')
    if purpose=='smartcal':                                                          # p.82: room conditions, lot number, the granulate on the pan
        sc=w['smartcal']; _need(rt.powered and rt.phase=='idle','PRECONDITION_FAILED')
        _need(sc['ambient'] is not None,'SC_AMBIENT_REQUIRED')
        _need(dev['settings']['sc.lot_input']!='Guided input' or sc['lot'],'SC_LOT_REQUIRED')
        _need(rt.smartcal is not None and rt.smartcal['released_kg']==0.0,'SC_NO_SUBSTANCE')
    else:
        _need(rt.powered and rt.phase=='idle' and rt._values()['sample.remaining_kg']>=.0001,'PRECONDITION_FAILED')
    if purpose=='qp_ref':
        qw=dev['qp_wizard']; _need(qw is not None and qw['stage']=='ready','QP_CONDITIONING')
    _start_check(rt,m,w)
    if m['params']['starting_mode']=='Manual':
        w['armed']=True; w['start_net_kg']=_net_kg(rt); w['stage']='armed'
        return {'phase':rt.phase,'operation_id':None}
    rt.lid_m=0.
    _begin_run(rt,m,w,_net_kg(rt))
    return {'phase':rt.phase,'operation_id':rt.operation_id}


def _run_params(m,w):
    p=copy.deepcopy(m['params'])
    if p['program'] in DRY:
        steps=[{k:p[k] for k in ('soc','soc_time_s','soc_free_mg','soc_free_mg_dt','soc_free_pct','soc_free_pct_dt')}|{'temperature':p['temperature']}]
    else:
        steps=copy.deepcopy(p['steps'])
    return dict(method_id=m['id'],version=m['version'],name=m['name'],params=p,steps=steps,test=w['test'],test_time_s=w['test_time_s'])


def _begin_run(rt,m,w,start_net):
    """Start drying with a stored method (terminal): the active slots show the method, the documented program drives the heater."""
    run=_run_params(m,w); p=run['params']
    rt.mode=p['program'].lower(); rt.target_k=float((p['temperature'] if p['program'] in DRY else p['steps'][-1]['temperature'])+273.15)
    rt.basis='dry' if p['display_mode']=='%AM' else 'wet'
    rt.duration_s=float(p['soc_time_s']) if (p['program'] in DRY and p['soc']=='Timed' and not run['test']) else 28800.
    rt._operation_count+=1; rt.operation_id=f'{rt.instance_id}:drying:{rt._operation_count}'
    rt.phase='running'; rt.elapsed_s=0.; rt.curve=[]
    run.update(start_net_kg=start_net,step=0,step_start_s=0.,soc_hits={},started=_now_dt(rt).isoformat(timespec='seconds'),prediction=None,
               interrupted_s=0.)
    run['purpose']=w.get('purpose')
    if run['purpose']=='tassist':
        ta=w['tassist']; run['tassist']=dict(reference=ta['reference'],max_c=ta['max_c'],hit_c=None,
                                             ramp_end_s=max(0.0,(ta['max_c']-TA_START_C)/TA_RATE_C_MIN*60.0))
    if run['purpose']=='qp_ref': rt.dev['qp_wizard']['stage']='measuring'
    rt.run=run; rt.dev['preheat']=None; w['stage']='running'; w['armed']=False; rt.start_kg=start_net
    rt._record()


@term('measurement.stop',obj({'save':BOOL}),obj({'phase':PHASE}),SCREEN+['heater'],'[Stop drying] > Abort measurement and save data：中止并保存已采数据（结果标记为中止）',
      ['cycle.phase','results.list','work.state'],{'save':True})
def _c_meas_stop(rt,a):
    _gate(rt); w=_work(rt,('running','armed'))
    if w['stage']=='armed':
        w.update(stage='sample',armed=False); return {'phase':rt.phase}
    _need(rt.phase=='running' and rt.run is not None,'PRECONDITION_FAILED')
    rt.phase='cancelled'; _finish_run(rt,aborted=True,save=a['save'])
    return {'phase':rt.phase}


@term('measurement.next_sample',obj({}),obj({'stage':TEXT}),SCREEN+['lid'],'[Next Sample]：同一方法测下一个样品（重新放配件与去皮）',['work.state'])
def _c_meas_next(rt,a):
    _gate(rt,idle=True); w=_work(rt,('done',)); w.update(stage='tare',note='',result_id=None,qp_saved=None)
    for k in rt.dev['settings']['ids.clear']: w['ids'][k]=''
    rt.lid_m=.1085
    return {'stage':'tare'}


@term('measurement.note',obj({'text':dict(type='string',maxLength=100)}),OK_OUT,SCREEN,'[Note]：给本次结果加注释（离开测量前）',['results.list','work.state'],
      {'text':'checked'})
def _c_meas_note(rt,a):
    _gate(rt); w=_work(rt,('done',)); w['note']=a['text']
    r=next((r for r in rt.dev['results'] if r['id']==w['result_id']),None)
    if r is not None: r['note']=a['text']
    return {'ok':True}


@term('measurement.set_id',obj({'name':dict(type='string',minLength=1,maxLength=20),'value':dict(type='string',maxLength=30)}),OK_OUT,SCREEN,
      '工作屏ID面板：输入标识值（注释），随结果保存',['work.state'],{'name':'Batch','value':'A1'})
def _c_meas_id(rt,a):
    _gate(rt); w=_work(rt); _need(rt.dev['settings']['ids.input']!='OFF','PRECONDITION_FAILED'); _need(a['name'] in rt.dev['ids'],'NOT_FOUND')
    w['ids'][a['name']]=a['value']
    return {'ok':True}


def _preheat_on(rt,m):
    p=m['params']
    rt.dev['preheat']=dict(kind=p['preheating'],target_c=p['preheat_temp'],until_s=rt.time_s+(3600. if p['preheating']=='Manual' else p['preheat_duration_min']*60.),
                           turnoff=p['preheat_turnoff'],started_s=rt.time_s)


@term('measurement.preheat',obj({'on':BOOL}),obj({'on':BOOL}),SCREEN+['heater','lid'],'方法预热（Manual：按提示启动/Stop preheating；最长1 h，p.49）',
      ['work.state'],{'on':True})
def _c_preheat(rt,a):
    _gate(rt,idle=True); w=_work(rt,('tare','sample','done')); m=_work_method(rt,w)
    _need(m['params']['preheating']!='OFF','PRECONDITION_FAILED')
    if a['on']: _preheat_on(rt,m)
    else: rt.dev['preheat']=None
    return {'on':a['on']}


@term('measurement.qp_action',obj({'action':{'type':'string','enum':['save_continue','save_stop']}}),obj({'saved':BOOL}),SCREEN,
      'QuickPredict：Save and Continue保存预测并继续，Save and Stop保存预测并停止（p.67）',['results.list','cycle.phase','work.state'],{'action':'save_continue'})
def _c_qp_action(rt,a):
    _gate(rt); w=_work(rt,('running',)); run=rt.run; pred=run and run.get('prediction')
    _need(pred is not None and pred['state']=='ok','NO_PREDICTION')
    p=run['params']; key='qp_allow_save' if a['action']=='save_continue' else 'qp_allow_stop'
    thr=p['qp_save_threshold' if a['action']=='save_continue' else 'qp_stop_threshold']
    _need(p[key] and (thr is None or pred['uncertainty']<=thr+1e-12),'PRECONDITION_FAILED')
    run['saved_prediction']=dict(pred)
    if a['action']=='save_stop':
        rt.phase='succeeded'; _finish_run(rt,aborted=False,save=True,predicted_only=True)
    return {'saved':True}


@term('result.delete',obj({'result_id':MID}),OK_OUT,SCREEN,'Results > 结果 > [Delete]（取决于用户权限）',['results.list'],{'result_id':1})
def _c_result_delete(rt,a):
    _gate(rt,'system_data_management'); dev=rt.dev
    _need(any(r['id']==a['result_id'] for r in dev['results']),'NOT_FOUND')
    dev['results']=[r for r in dev['results'] if r['id']!=a['result_id']]
    return {'ok':True}


# ---- tests and adjustments (p.75-87)
PROC_KINDS=('weight_test_internal','weight_test_external','repeatability_external','temperature_test','weight_adjustment_internal',
            'weight_adjustment_external','temperature_adjustment')
@term('procedure.start',obj({'kind':{'type':'string','enum':list(PROC_KINDS)}}),obj({'stage':TEXT}),SCREEN+['balance_sensor','heater','temperature_sensor'],
      'Test/Adjust：开始测试或调整（内/外部砝码测试、重复性、温度测试、内/外部砝码调整、温度调整）；需要的砝码或温度套件由宿主放入',
      ['procedure.state','tests.history','work.state'],{'kind':'weight_test_internal'})
def _c_proc_start(rt,a):
    k=a['kind']; dev=rt.dev
    _gate(rt,'run_adjustments' if 'adjustment' in k else 'run_tests',idle=True)
    _need(dev['work'] is None or dev['work']['stage']!='running','BUSY')
    s=dev['settings']; empty=_gross_kg(rt)<1e-6
    proc=dict(kind=k,stage='done',started_s=rt.time_s,readings=[],result=None,prompt='',nominal_g=None)
    if k in ('weight_test_internal','weight_adjustment_internal'):
        _need(empty,'PAN_NOT_CLEAR')
        if k=='weight_test_internal':
            proc['result']=_internal_test(rt)
        else:
            proc['result']=_internal_adjust(rt,'Weight adjustment - internal')
    elif k in ('weight_test_external','weight_adjustment_external','repeatability_external'):
        _need(empty,'PAN_NOT_CLEAR')
        tw='tw1' if s['wt_ext.weight' if k!='weight_adjustment_external' else 'wa_ext.weight']=='Test weight 1' else 'tw2'
        nominal=s[tw+'.nominal']
        if k=='weight_adjustment_external': _need(nominal>=30.0,'WEIGHT_TOO_SMALL')   # p.100: adjustment weights at least 30 g
        proc.update(stage='load',nominal_g=nominal,weight=tw,prompt=f'{_fmt_dec(nominal,2)} g',load_since=None)
    else:
        _need(rt.kit_installed,'NO_TEMPERATURE_KIT'); _need(rt.lid_m<=1e-6,'CHAMBER_OPEN')
        _need(rt.temperature_k-273.15<50.0,'TOO_HOT')                                   # p.78, p.83: cooled down below 50 degC
        if k=='temperature_test':
            temps=[(s['ta.t1'],s['tt.t1_tol'],'Temperature 1')] if s['tt.t1'] else []
            temps+=[(s['ta.t2'],s['tt.t2_tol'],'Temperature 2')] if s['tt.t2'] else []
            temps+=[(s['tt.t3_temp'],s['tt.t3_tol'],'Temperature 3')] if s['tt.t3'] else []
            _need(temps,'INVALID_ARGUMENT')
        else:
            temps=[(s['ta.t1'],None,'Temperature 1'),(s['ta.t2'],None,'Temperature 2')]
        proc.update(stage='heating',plateaus=temps,index=0,plateau_s=900.,phase_start_s=rt.time_s)
    dev['proc']=proc
    if proc['stage']=='done': _close_proc(rt)
    return {'stage':proc['stage']}


@term('procedure.cancel',obj({}),OK_OUT,SCREEN,'中止测试/调整（中止的不存入历史，p.86）',['procedure.state'])
def _c_proc_cancel(rt,a):
    _gate(rt); _need(rt.dev['proc'] is not None,'PRECONDITION_FAILED')
    rt.dev['last_proc']=dict(rt.dev['proc'],stage='aborted'); rt.dev['proc']=None
    return {'ok':True}


@term('procedure.finish',obj({}),obj({'stage':TEXT}),SCREEN,'重复性测试：至少3次后[Finish]给出结果（结果不能保存，只能打印，p.77）',['procedure.state'])
def _c_proc_finish(rt,a):
    _gate(rt); pr=rt.dev['proc']; _need(pr is not None and pr['kind']=='repeatability_external' and len(pr['readings'])>=3,'PRECONDITION_FAILED')
    r=pr['readings']; mean=sum(r)/len(r); sd=math.sqrt(sum((x-mean)**2 for x in r)/(len(r)-1))
    pr.update(stage='done',result=dict(n=len(r),mean_g=mean,sd_g=sd,min_g=min(r),max_g=max(r),passed=None)); _close_proc(rt,save=False)
    return {'stage':'done'}


@term('procedure.dismiss',obj({}),OK_OUT,SCREEN,'关闭测试/调整结果屏',['procedure.state'])
def _c_proc_dismiss(rt,a):
    _gate(rt); rt.dev['last_proc']=None
    return {'ok':True}


def _hist(rt,key,entry):
    h=rt.dev['history'][key]; h.append(dict(entry,time=_now_dt(rt).isoformat(timespec='seconds'),user=rt.dev['current_user'] or '-',
                                              level='level' if rt._level_ok() else 'out of level'))
    del h[:-MAX_HISTORY]


def _internal_test(rt):
    e=rt.sensor; actual=INTERNAL_WEIGHT_G*(1+e['internal_weight_rel'])*(1+e['span_rel'])*rt.cal['span']
    diff=(actual/INTERNAL_WEIGHT_G-1)*100.0; tol=rt.dev['settings']['wt_int.tol']
    res=dict(kind='Weight test - internal',nominal_pct=100.0,actual_pct=100.0+diff,diff_pct=diff,tol_pct=tol,passed=abs(diff)<=tol+1e-12,
             temperature_c=round(rt.temperature_k-273.15,1))
    _hist(rt,'weight_test',res); return res


def _internal_adjust(rt,label):
    e=rt.sensor; rt.cal['span']=1.0/((1+e['internal_weight_rel'])*(1+e['span_rel']))
    res=dict(kind=label,temperature_c=round(rt.temperature_k-273.15,1),passed=True)
    _hist(rt,'weight_adjustment',res); return res


def _history_text(e):
    """Detail lines of a test / adjustment record (p.84-85)."""
    if e is None: return ''
    out=[e.get('kind','')]
    for k,label,unit in (('weight_name','Test weight',''),('nominal_g','Nominal weight',' g'),('actual_g','Actual weight',' g'),('diff_g','Difference',' g'),
                         ('tol_g','Tolerance',' g'),('actual_pct','Actual weight',' %'),('diff_pct','Difference',' %'),('tol_pct','Tolerance',' %'),
                         ('temperature_c','Temperature',' °C'),('mean_g','Mean',' g'),('sd_g','Standard deviation',' g'),('n','Number of weighings',''),
                         ('test','SmartCal test',''),('lot','SmartCal identification',''),('start_g','Start weight',' g'),('test_min','Total test time',' min'),
                         ('result_mcn','Result',' %MCN'),('lower','Lower limit',' %MCN'),('upper','Upper limit',' %MCN'),('room_c','Room temperature',' °C'),
                         ('room_rh','Room humidity',' %rH')):
        if e.get(k) is not None:
            v=e[k]; out.append(f'{label}: {v:.5g}{unit}' if isinstance(v,float) else f'{label}: {v}{unit}')
    for pt in e.get('points',[]):
        out.append(f"{pt['label']}: target {pt['target_c']:.0f} °C, sensor {pt['sensor_c']:.1f} °C, kit {pt['actual_c']:.1f} °C")
    if e.get('level'): out.append(f"Leveling: {e['level']}")
    if e.get('passed') is not None: out.append('passed' if e['passed'] else 'failed')
    return '\n'.join(out)


def _close_proc(rt,save=True):
    rt.dev['last_proc']=rt.dev['proc']; rt.dev['proc']=None


def _proc_step(rt):
    """Advance a running test / adjustment on the host clock (load / unload prompts, 15 min temperature plateaus, 75 s weight time-out)."""
    pr=rt.dev['proc']
    if pr is None: return
    k=pr['kind']; s=rt.dev['settings']
    if pr['stage'] in ('load','unload'):
        nominal=pr['nominal_g']; load_g=_gross_kg(rt)*1000.0; reading=_corrected_kg(rt)*1000.0
        if pr['stage']=='load':
            if abs(load_g-nominal)<=0.05*nominal:                                       # the requested weight is on the pan
                pr['load_since']=pr['load_since'] if pr['load_since'] is not None else rt.time_s
                if rt.time_s-pr['load_since']>=2.0-1e-9:                                # stable for 2 s (assumption)
                    pr['readings'].append(reading); pr['stage']='unload'; pr['prompt']='000.00'; pr['unload_since']=None
            else:
                pr['load_since']=None
                if k!='repeatability_external' and rt.time_s-pr['started_s']>75.0:       # p.82: time-out over 75 s
                    pr.update(stage='error',prompt='Weight out of range. Adjustment aborted.' if 'adjustment' in k else 'Weight out of range. Test aborted.')
                    _close_proc(rt)
        elif load_g<=0.005*nominal:
            if k=='repeatability_external':
                pr.update(stage='load',prompt=f'{_fmt_dec(nominal,2)} g',load_since=None)
                return
            actual=pr['readings'][-1]; tw=pr['weight']
            if k=='weight_test_external':
                tol=s['wt_ext.tol']; diff=actual-nominal
                res=dict(kind='Weight test - external',weight_name=s[tw+'.name'],weight_id=s[tw+'.id'],nominal_g=nominal,actual_g=actual,diff_g=diff,tol_g=tol,
                         passed=abs(diff)<=tol+1e-12,temperature_c=round(rt.temperature_k-273.15,1))
                _hist(rt,'weight_test',res)
            else:
                rt.cal['span']*=nominal/actual                                           # the reading of the test weight becomes its nominal value
                res=dict(kind='Weight adjustment - external',weight_name=s[tw+'.name'],weight_id=s[tw+'.id'],nominal_g=nominal,
                         temperature_c=round(rt.temperature_k-273.15,1),passed=True)
                _hist(rt,'weight_adjustment',res)
            pr.update(stage='done',result=res); _close_proc(rt)
    elif pr['stage']=='heating':
        if rt.time_s-pr['phase_start_s']>=pr['plateau_s']-1e-9:
            target,tol,label=pr['plateaus'][pr['index']]
            pr['readings'].append(dict(label=label,target_c=float(target),sensor_c=rt._sensor_c(),actual_c=rt.temperature_k-273.15,tol_c=tol))
            pr['index']+=1; pr['phase_start_s']=rt.time_s
            if pr['index']>=len(pr['plateaus']):
                if k=='temperature_test':
                    passed=all(abs(r['sensor_c']-r['actual_c'])<=r['tol_c']+1e-9 for r in pr['readings'])
                    res=dict(kind='Temperature test',kit=s['tkit.name'],kit_id=s['tkit.id'],points=pr['readings'],passed=passed); _hist(rt,'temperature_test',res)
                else:
                    (r1,r2)=pr['readings'][:2]
                    raw1,raw2=rt._raw_c(r1['actual_c']),rt._raw_c(r2['actual_c'])
                    a_=(r2['actual_c']-r1['actual_c'])/(raw2-raw1) if abs(raw2-raw1)>1e-9 else 1.0
                    rt.cal['temp_a'],rt.cal['temp_b']=a_,r1['actual_c']-a_*raw1        # the sensor reading now equals the kit temperature
                    res=dict(kind='Temperature adjustment',kit=s['tkit.name'],kit_id=s['tkit.id'],points=pr['readings'],passed=True)
                    _hist(rt,'temperature_adjustment',res)
                pr.update(stage='done',result=res); _close_proc(rt)


# ---- Method Wizard, temperature assistant (p.51-55), QuickPredict Wizard (p.60-61), SmartCal x1 test (p.81-83, p.103-104, p.87)
WIZ_ANSWERS=obj({'sample_type':{'type':'string','enum':['liquid','paste','solid','skin']},'sensitivity':{'type':'string','enum':['robust','sensitive']},
                 'moisture':{'type':'string','enum':['low','medium','high']},'priority':{'type':'string','enum':['speed','precision']}})
WIZ_PREP={'liquid':'Spread the sample evenly over a glass fibre filter.','paste':'Spread a thin even layer; cover with a glass fibre filter.',
          'solid':'Grind coarse samples; spread evenly 2-5 mm thick over the pan.','skin':'Cover with a glass fibre filter to avoid a skin or crust.'}


def _wizard_proposal(a):
    """Method Wizard step 1 (p.51): method parameters proposed from the sample characteristics (engineering rules; the questionnaire itself is not
    printed in the manual): 105 degC (80 degC for sensitive samples), Gentle for skin-forming or sensitive pastes, Rapid for robust samples with much
    moisture, SOC 2 (speed) / 4 (precision; 5 for little moisture), start weight 5 / 3 / 2 g for low / medium / high moisture, a glass fibre filter for
    liquids, pastes and skin-forming samples, and the sample preparation text."""
    sens=a['sensitivity']=='sensitive'
    program='Gentle' if a['sample_type']=='skin' or (sens and a['sample_type']=='paste') else ('Rapid' if a['moisture']=='high' and not sens else 'Standard')
    soc='2' if a['priority']=='speed' else ('5' if a['moisture']=='low' else '4')
    return dict(program=program,temperature=80 if sens else 105,soc=soc,start_weight=True,start_weight_g={'low':5.0,'medium':3.0,'high':2.0}[a['moisture']],
                start_weight_tol=10,accessory='Sample pan',acc_filter=a['sample_type']!='solid',sample_preparation=WIZ_PREP[a['sample_type']])


def _ta_tol(ref): return max(0.05,0.02*abs(ref))                                    # assumption: agreement with the reference value


def _wiz(rt,steps=None):
    w=rt.dev['wizard']; _need(w is not None,'NO_WIZARD')
    if steps: _need(w['step'] in steps,'PRECONDITION_FAILED')
    return w


@term('wizard.start',obj({'name':NAME30}),obj({'step':TEXT}),SCREEN,
      'Method Definition > New... > Define using Method Wizard（p.51）：确认免责声明并命名后开始向导（新方法草稿，Finish后在参数菜单Save才存储）',
      ['method.draft','wizard.state'],{'name':'Wizard method'})
def _c_wiz_start(rt,a):
    _gate(rt,'method_definition',idle=True); dev=rt.dev
    _need(len(dev['methods'])<MAX_METHODS,'MEMORY_FULL')
    _need(not any(m['name']==a['name'] for m in dev['methods']),'NAME_EXISTS')
    dev['draft']=dict(_new_method(None,a['name']),base_version=None)
    dev['wizard']=dict(step='questionnaire',answers=None,proposal=None,reference=None,tassist=None,test=None)
    return {'step':'questionnaire'}


@term('wizard.answer',WIZ_ANSWERS,obj({'proposal':dict(type='object')}),SCREEN,
      'Method Wizard第1步问卷（p.51）：样品形态、温度敏感性、预期水分、速度/精度优先；按工程规则提出方法参数并写入草稿（问卷内容手册未给出，为假设）',
      ['method.draft','wizard.state'],{'sample_type':'solid','sensitivity':'robust','moisture':'medium','priority':'precision'})
def _c_wiz_answer(rt,a):
    _gate(rt,'method_definition',idle=True); w=_wiz(rt,('questionnaire','proposal')); d=rt.dev['draft']; _need(d is not None,'NO_DRAFT')
    prop=_wizard_proposal(a); d['params'].update(copy.deepcopy(prop))
    w.update(step='proposal',answers=dict(a),proposal=prop)
    return {'proposal':copy.deepcopy(prop)}


@term('wizard.choose',obj({'next':{'type':'string','enum':['temperature_assistant','test_measurement','accept','restart']}}),obj({'step':TEXT}),SCREEN,
      'Method Wizard的选择（p.51-52）：继续温度助手 / 继续测试测量 / 接受方法参数并退出向导（回到参数菜单，Save才存储）/ 用不同干燥温度重新开始',
      ['wizard.state'],{'next':'temperature_assistant'})
def _c_wiz_choose(rt,a):
    _gate(rt,'method_definition',idle=True); w=_wiz(rt); n=a['next']
    if n=='accept':
        _need(w['step'] in ('proposal','tassist_done','test_done','adopted'),'PRECONDITION_FAILED'); rt.dev['wizard']=None
        return {'step':'finished'}
    if n=='temperature_assistant' or n=='restart':
        _need(w['step'] in (('proposal','tassist_done') if n=='temperature_assistant' else ('test_done','adopted')),'PRECONDITION_FAILED'); w['step']='tassist'
    else:
        _need(w['step']=='tassist_done','PRECONDITION_FAILED'); w['step']='test'
    return {'step':w['step']}


@term('tassist.start',obj({'reference':number('1',0.01,1000),'max_temperature_c':dict(type='integer',minimum=40,maximum=230,**{'x-unit':'°C'})}),
      obj({'stage':TEXT}),SCREEN+['lid'],
      'Temperature assistant（p.54）：输入参考值与可接受的最高干燥温度后打开工作屏，像普通测量一样完成测试；成功时把建议干燥温度写入草稿（未保存），失败提示重做',
      ['work.state','lid.position_m','wizard.state'],{'reference':10.0,'max_temperature_c':160})
def _c_tassist(rt,a):
    _gate(rt,'method_definition',idle=True); dev=rt.dev; d=dev['draft']; _need(d is not None,'NO_DRAFT')
    w=dev['wizard']
    if w is not None: _need(w['step']=='tassist','PRECONDITION_FAILED'); w['reference']=a['reference']
    _open_work(rt,d['id'],purpose='tassist',draft=True,tassist=dict(reference=a['reference'],max_c=float(a['max_temperature_c'])))
    return {'stage':'tare'}


@term('wizard.test_start',obj({'test_time_s':{'type':['number','null'],'minimum':30,'maximum':28800,'x-unit':'s'}}),obj({'stage':TEXT}),SCREEN+['lid'],
      'Method Wizard第3步测试测量（p.52, p.54-55）：用草稿参数做测试测量（记录关断标准1-5到达点），之后推荐最接近参考值的关断标准',
      ['work.state','lid.position_m','wizard.state'],{'test_time_s':None})
def _c_wiz_test(rt,a):
    _gate(rt,'method_definition',idle=True); w=_wiz(rt,('test',)); d=rt.dev['draft']; _need(d is not None,'NO_DRAFT')
    _open_work(rt,d['id'],purpose='wizard_test',test=True,test_time_s=a['test_time_s'],draft=True)
    return {'stage':'tare'}


@term('wizard.adopt',obj({'soc':{'type':'string','enum':['1','2','3','4','5']}}),obj({'soc':TEXT}),SCREEN,
      'Method Wizard：点选测试测量中希望采用的关断标准（p.52），写入草稿；之后Finish回到参数菜单',['method.draft','wizard.state'],{'soc':'3'})
def _c_wiz_adopt(rt,a):
    _gate(rt,'method_definition',idle=True); w=_wiz(rt,('test_done','adopted')); d=rt.dev['draft']; _need(d is not None,'NO_DRAFT')
    _need(a['soc'] in w['test']['hits'],'PRECONDITION_FAILED')
    if d['params']['program'] in DRY: d['params']['soc']=a['soc']
    else: d['params']['steps'][-1]['soc']=a['soc']
    w['step']='adopted'; w['adopted']=a['soc']
    return {'soc':a['soc']}


def _qp_wizard_abort(rt):
    dev=rt.dev; qw=dev['qp_wizard']; d=dev['draft']
    if d is not None and qw is not None: d['qp_refs'],d['qp_knowledge']=qw['saved']
    dev['qp_wizard']=None


@term('qp_wizard.start',obj({}),obj({'schedule':dict(type='array',items=TEXT)}),SCREEN+['lid','heater'],
      'QuickPredict Wizard（p.60-61）：在方法参数菜单的QuickPredict中开始记录参考曲线；按仪器冷/热状态排出最快的3冷+3热顺序，按需升温或冷却后打开参考测量工作屏',
      ['qp_wizard.state','method.draft','work.state','lid.position_m'])
def _c_qpw_start(rt,a):
    _gate(rt,'method_definition',idle=True); dev=rt.dev; d=dev['draft']; _need(d is not None,'NO_DRAFT'); _need(d['id'] is not None,'NOT_FOUND')
    _need(dev['settings']['qp.enabled'] and d['params']['qp'],'QP_DISABLED'); _need(not _qp_issues(d['params']),'QP_INCOMPATIBLE')
    cold=rt._sensor_c()<=QP_COLD_C
    sched=['cold','warm','warm','warm','cold','cold'] if cold else ['warm','warm','warm','cold','cold','cold']
    dev['qp_wizard']=dict(method_id=d['id'],schedule=sched,index=0,stage='conditioning',since=None,last=None,refs=[],
                          saved=(copy.deepcopy(d['qp_refs']),copy.deepcopy(d['qp_knowledge'])))
    d['qp_refs'],d['qp_knowledge']=[],None
    _open_work(rt,d['id'],purpose='qp_ref',draft=True)
    _qp_wizard_tick(rt)
    return {'schedule':list(sched)}


@term('qp_wizard.finish',obj({}),obj({'qp_usage':TEXT}),SCREEN,
      'QuickPredict Wizard：预测知识计算完成后[OK]、[Finish]，方法的QuickPredict usage变为Active（须在参数菜单Save才存储参考曲线与预测知识，p.61）',
      ['qp_wizard.state','method.draft','work.state'])
def _c_qpw_finish(rt,a):
    _gate(rt,'method_definition',idle=True); dev=rt.dev; qw=dev['qp_wizard']; _need(qw is not None and qw['stage']=='calculated','PRECONDITION_FAILED')
    d=dev['draft']; d['params']['qp_usage']='Active'; dev['qp_wizard']=None; dev['work']=None
    d['qp_recorded_with']={k:copy.deepcopy(d['params'].get(k)) for k in RESULT_KEYS}
    return {'qp_usage':'Active'}


@term('qp_wizard.cancel',obj({}),OK_OUT,SCREEN,'中止QuickPredict Wizard（已记录的参考曲线丢弃，恢复原有参考数据）',['qp_wizard.state','method.draft','work.state'])
def _c_qpw_cancel(rt,a):
    _gate(rt,'method_definition',idle=True); _need(rt.dev['qp_wizard'] is not None,'PRECONDITION_FAILED')
    _qp_wizard_abort(rt); rt.dev['work']=None
    return {'ok':True}


def _qp_wizard_tick(rt):
    """QuickPredict Wizard conditioning (p.60): a cold start needs the instrument at or below 40 degC (heater off), a warm start the drying temperature
    held for 5 min (assumptions); the remaining time is shown as a timer."""
    qw=rt.dev['qp_wizard']
    if qw is None or qw['stage'] not in ('conditioning','ready'): return
    d=rt.dev['draft']; kind=qw['schedule'][qw['index']]; c=rt._sensor_c()
    if kind=='cold':
        qw['since']=None
        qw['stage']='ready' if c<=QP_COLD_C+1e-9 else 'conditioning'
        amb=25.0; qw['timer_s']=0.0 if c<=QP_COLD_C else 45.0*math.log(max(1.0,(c-amb)/(QP_COLD_C-amb)))
    else:
        target=float(d['params']['temperature']) if d else 105.0
        if abs(c-target)<=2.0:
            qw['since']=rt.time_s if qw['since'] is None else qw['since']
        else:
            qw['since']=None
        held=0.0 if qw['since'] is None else rt.time_s-qw['since']
        if qw['stage']=='conditioning' and held>=QP_WARM_HOLD_S-1e-9: qw['stage']='ready'
        qw['timer_s']=0.0 if qw['stage']=='ready' else (QP_WARM_HOLD_S-held if qw['since'] is not None else QP_WARM_HOLD_S+45.0*3)


def _sc_temp(s,i): return float(SC_FIXED[i-1] if i<=4 else s[f'sc.m{i}.temp'])


def _sc_limits(s,i): return [s[f'sc.m{i}.lo'],s[f'sc.m{i}.hi']] if s[f'sc.m{i}.limits'] else None


def _sc_mc(t_c):
    """Moisture content (%) the SmartCal granulate releases at the sample temperature t_c (degC) after the test time: linear through the centres of the
    recommended control windows (p.104), extrapolated with the end slopes (assumption)."""
    xs=list(SC_FIXED); ys=[sum(SC_LIMITS[t])/2 for t in xs]
    if t_c<=xs[0]: return ys[0]+(t_c-xs[0])*(ys[1]-ys[0])/(xs[1]-xs[0])
    if t_c>=xs[-1]: return ys[-1]+(t_c-xs[-1])*(ys[-1]-ys[-2])/(xs[-1]-xs[-2])
    i=bisect.bisect_right(xs,t_c)-1
    return ys[i]+(t_c-xs[i])*(ys[i+1]-ys[i])/(xs[i+1]-xs[i])


def _smartcal_method(rt,i):
    p=_defaults(HX_METHOD_FIELDS); t=_sc_temp(rt.dev['settings'],i)
    p.update(name=f'SmartCal {t:.0f} °C',program='Standard',temperature=int(t),soc='Timed',soc_time_s=int(SC_TEST_S),display_mode='%MC',steps=copy.deepcopy(STEP_DEFAULTS))
    return dict(id=None,name=p['name'],version=1,params=p)


def _smartcal_view(rt):
    w=rt.dev['work']
    if w is None or w.get('purpose')!='smartcal': return None
    sc=w['smartcal']; s=rt.dev['settings']
    return dict(test=sc['test'],name=_smartcal_method(rt,sc['test'])['name'],temperature=_sc_temp(s,sc['test']),limits=_sc_limits(s,sc['test']),
                ambient=copy.deepcopy(sc['ambient']),lot=sc['lot'],lot_input=s['sc.lot_input'],substance=rt.smartcal is not None,
                result=copy.deepcopy(sc.get('result')))


@term('smartcal.open',obj({'test':dict(type='integer',minimum=1,maximum=6,**{'x-unit':'1'})}),obj({'stage':TEXT}),SCREEN+['lid'],
      'Test/Adjust > Tests > SmartCal x1 test（p.82）：从方法列表选择一个启用的SmartCal方法（70/100/130/160 °C或自定义温度），打开工作屏（样品室打开）',
      ['work.state','smartcal.state','lid.position_m'],{'test':2})
def _c_sc_open(rt,a):
    _gate(rt,'run_tests',idle=True); s=rt.dev['settings']; _need(s[f"sc.m{a['test']}.on"],'NOT_FOUND')
    _open_work(rt,None,purpose='smartcal',smartcal=dict(test=a['test'],ambient=None,lot='',result=None))
    return {'stage':'tare'}


@term('smartcal.ambient',obj({'temperature_c':number('°C',5,40),'humidity_pct':number('%',0,100)}),OK_OUT,SCREEN,
      'SmartCal工作屏[Enter room temperature and humidity]（无RHT传感器时，p.82）：输入室温与相对湿度（用于结果归一化）',['smartcal.state'],
      {'temperature_c':23.0,'humidity_pct':45.0})
def _c_sc_ambient(rt,a):
    _gate(rt); w=_work(rt,('tare','sample')); _need(w.get('purpose')=='smartcal','PRECONDITION_FAILED')
    w['smartcal']['ambient']=dict(temperature_c=a['temperature_c'],humidity_pct=a['humidity_pct'])
    return {'ok':True}


@term('smartcal.lot',obj({'lot':dict(type='string',maxLength=30)}),OK_OUT,SCREEN,'SmartCal工作屏：输入SmartCal批号（Lot number input为Guided input时必须，p.82, p.103）',
      ['smartcal.state'],{'lot':'L12345'})
def _c_sc_lot(rt,a):
    _gate(rt); w=_work(rt,('tare','sample')); _need(w.get('purpose')=='smartcal' and rt.dev['settings']['sc.lot_input']!='OFF','PRECONDITION_FAILED')
    w['smartcal']['lot']=a['lot']
    return {'ok':True}


@term('smartcal.place',obj({'mass_kg':number('kg',0.0005,0.01),'room_rh_pct':number('%',0,100)}),obj({'placed':BOOL}),['sample_pan','balance_sensor'],
      '受信任宿主：拆开一支SmartCal把颗粒均匀撒在去皮后的铝盘上（p.82；room_rh_pct为颗粒存放环境的真实湿度）',['smartcal.sample','smartcal.state','balance.indicated_kg'],
      {'mass_kg':0.0025,'room_rh_pct':50.0},kind='configuration',visibility='evaluator')
def _c_sc_place(rt,a):
    _need(rt.phase!='running','BUSY'); _need(rt.lid_m>=.1-1e-9,'CHAMBER_CLOSED')
    rt.smartcal=dict(mass_kg=a['mass_kg'],released_kg=0.0,room_rh_pct=a['room_rh_pct'])
    return {'placed':True}


def _purpose_finish(rt,run,res,aborted):
    """End of a wizard / assistant / reference / SmartCal run: the procedure's own evaluation (not stored in the results journal)."""
    dev=rt.dev; w=dev['work']; p=run['params']; kind=run['purpose']; value=res['value']
    if kind=='tassist':
        ta=run['tassist']; ref=ta['reference']; tol=_ta_tol(ref)
        if aborted: out=dict(passed=False,message='Test aborted.')
        elif value>ref+tol: out=dict(passed=False,message='The final moisture content is higher than the reference value: repeat the test with a lower '
                                                         'maximum drying temperature.')
        elif value<ref-tol or ta['hit_c'] is None: out=dict(passed=False,message='The reference value was not reached: repeat the test with a higher '
                                                                                 'maximum drying temperature.')
        else:
            prop=int(min(ta['max_c'],max(40.0,5.0*math.ceil(ta['hit_c']/5.0-1e-9))))
            out=dict(passed=True,proposed_c=prop,message=f'Proposed drying temperature: {prop} °C')
            d=dev['draft']
            if d is not None:
                if d['params']['program'] in DRY: d['params']['temperature']=prop
                else: d['params']['steps'][-1]['temperature']=prop
        out.update(reference=ref,max_c=ta['max_c'],final=value,text=res['text'])
        dev['assistant']=out
        wz=dev['wizard']
        if wz is not None and wz['step']=='tassist':
            wz['tassist']=dict(out)
            if out['passed']: wz['step']='tassist_done'
    elif kind=='wizard_test':
        wz=dev['wizard']; hits=dict(run['soc_hits'])
        ref=wz['reference'] if wz else None
        rec=min(hits,key=lambda k:(abs(hits[k]['value']-ref),k)) if (hits and ref is not None) else ('3' if '3' in hits else (min(hits) if hits else None))
        if wz is not None:
            wz['test']=dict(hits=hits,recommended=rec,aborted=aborted)
            if hits and not aborted: wz['step']='test_done'
    elif kind=='qp_ref':
        qw=dev['qp_wizard']; d=dev['draft']
        if qw is None or d is None: return
        ww=run['start_net_kg']*1000.0; dw=res['dry_g']; loss=(ww-dw)/ww if ww>0 else 0.0
        reason=('Measurement aborted.' if aborted else 'Drying curve too short (less than 2 min).' if rt.elapsed_s<QP_MIN_S
                else 'Weight loss too small (below 1 %).' if loss<0.01 else None)
        if reason is None:
            t63=next((c['time_s'] for c in rt.curve if (ww-c['mass_kg']*1000.0)>=0.632*(ww-dw)),rt.elapsed_s)
            d['qp_refs'].append(dict(final=value,loss=loss,tau_s=max(t63,1.0),start=qw['schedule'][qw['index']]))
            qw['index']+=1
        qw['last']=dict(accepted=reason is None,reason=reason,text=res['text'],n=qw['index'])
        if qw['index']>=6:                                                             # p.61: the prediction knowledge from the six reference curves
            fin=[r['final'] for r in d['qp_refs']]; mean=sum(fin)/6.; sd=math.sqrt(sum((x-mean)**2 for x in fin)/5.)
            d['qp_knowledge']=dict(tau_s=sum(r['tau_s'] for r in d['qp_refs'])/6.,sd=sd); qw['stage']='calculated'
        else:
            qw['stage']='conditioning'; qw['since']=None
    elif kind=='smartcal':
        sc=w['smartcal']; s=dev['settings']; i=sc['test']; lim=_sc_limits(s,i)
        if aborted: sc['result']=dict(aborted=True); return
        rh=sc['ambient']['humidity_pct']; mcn=round(value-SC_RH_COEF*(rh-SC_RH_REF),2)          # normalised to 50 %rH (assumption)
        passed=None if lim is None else lim[0]-1e-9<=mcn<=lim[1]+1e-9
        entry=dict(kind='SmartCal x1 test',test=_smartcal_method(rt,i)['name'],test_no=i,lot=sc['lot'],start_g=res['start_g'],test_min=round(rt.elapsed_s/60.0,2),
                   result_mcn=mcn,lower=lim[0] if lim else None,upper=lim[1] if lim else None,room_c=sc['ambient']['temperature_c'],room_rh=rh,passed=passed,
                   time=_now_dt(rt).isoformat(timespec='seconds'),user=dev['current_user'] or '-',level='level' if rt._level_ok() else 'out of level')
        h=dev['history']['smartcal']; h.append(entry)
        same=[e for e in h if e['test_no']==i]
        if len(same)>SC_HISTORY: h.remove(same[0])                                      # p.82: the oldest result of this test is deleted
        sc['result']=dict(entry)
        if s['sc.clear_lot']: sc['lot']=''


# ---- system (p.17, p.103, p.110)
@term('quickpredict.establish',obj({'method_id':MID}),obj({'state':TEXT}),SCREEN,
      'QuickPredict Wizard / Reference Curves：开始为方法记录6条参考曲线（之后该方法的测量记为参考曲线，满6条计算预测知识）',['methods.list','method.draft'],
      {'method_id':1})
def _c_qp_establish(rt,a):
    _gate(rt,'method_definition',idle=True); m=_method(rt.dev,a['method_id']); _need(m is not None,'NOT_FOUND')
    _need(m['params']['qp'],'QP_DISABLED')
    m['qp_recording']=True; m['qp_refs']=[] if m['qp_knowledge'] is None else m['qp_refs']
    if rt.dev['draft'] and rt.dev['draft']['id']==m['id']: rt.dev['draft']['qp_recording']=True
    return {'state':'recording'}


@term('system.reset',obj({}),OK_OUT,SCREEN,'Reset：恢复出厂设置（方法、设置、结果、用户丢失；日期时间与调整/历史保留；随后出现Setup wizard）',
      ['device.settings','methods.list','results.list','users.state','work.state'])
def _c_reset(rt,a):
    _gate(rt,'system_data_management',idle=True); dev=rt.dev
    keep=dict(history=dev['history'],clock_offset_s=dev['clock_offset_s'],power_epoch=dev['power_epoch'],last_fact_s=dev['last_fact_s'])
    rt.dev=_new_device(); rt.dev.update(copy.deepcopy(keep)); rt.dev['setup_done']=False
    return {'ok':True}


@term('setup.finish',obj({'language':{'type':'string','enum':list(HX_LANGUAGES)},'date_format':{'type':'string','enum':list(HX_DATE_FORMATS)}}),OK_OUT,SCREEN,
      'Setup Wizard：选择语言与日期格式并安装样品室后完成（未完成则仪器不可用，p.17）',['users.state','device.settings'],
      {'language':'English','date_format':'D. MMM YYYY'})
def _c_setup(rt,a):
    _gate(rt,'setup'); dev=rt.dev; _need(not dev['setup_done'],'PRECONDITION_FAILED')
    dev['settings']['default.language']=a['language']; dev['settings']['region.date_format']=a['date_format']
    for u in dev['users']: u['prefs']['language']=a['language']
    dev['setup_done']=True
    return {'ok':True}


# ---- host / evaluator configuration of the scene
@term('pan.load',obj({'mass_kg':number('kg',0,.201)}),obj({'mass_kg':number('kg')}),['pan_holder','sample_pan','balance_sensor'],
      '受信任宿主：盘架上除样品外的物体质量（样品盘、玻璃纤维滤片、测试砝码）',['balance.indicated_kg'],{'mass_kg':.0045},kind='configuration',visibility='evaluator')
def _c_pan_load(rt,a):
    _need(rt.phase!='running' or a['mass_kg']==rt.pan_load_kg,'BUSY')
    rt.pan_load_kg=a['mass_kg']
    return {'mass_kg':rt.pan_load_kg}


@term('sample.remove',obj({}),obj({'removed':BOOL}),['sample_pan','balance_sensor'],'受信任宿主：取出样品（样品账本清空；干燥中不可）',
      ['sample.initial_kg','sample.remaining_kg','sample.evaporated_kg','balance.indicated_kg','smartcal.sample','smartcal.state'],kind='configuration',visibility='evaluator')
def _c_sample_remove(rt,a):
    _need(rt.phase!='running','BUSY')
    rt.smartcal=None
    rt.initial_species={}; rt.remaining={}; rt.evaporated={}; rt.residue_kg=0.; rt.curve=[]; rt.phase='idle'; rt.elapsed_s=0.; rt.operation_id=None
    rt.start_kg=None
    return {'removed':True}


@term('temperature_kit.set',obj({'installed':BOOL}),obj({'installed':BOOL}),['chamber_floor','temperature_sensor'],'受信任宿主：插入/取出温度调整套件（温度测试与调整用）',
      ['temperature_kit.installed'],{'installed':True},kind='configuration',visibility='evaluator')
def _c_kit(rt,a):
    pr=rt.dev['proc']
    _need(a['installed'] or pr is None or 'plateaus' not in pr,'BUSY')               # not removed during a temperature test / adjustment
    rt.kit_installed=a['installed']
    return {'installed':rt.kit_installed}


@term('level.set_tilt',obj({'x_deg':number('deg',-5,5),'y_deg':number('deg',-5,5)}),obj({'ok':BOOL}),['level_indicator','housing'],
      '受信任宿主：干燥单元倾斜（内置倾角传感器检测不水平并显示状态图标，p.19, p.26）',['level.state'],{'x_deg':0.0,'y_deg':0.0},kind='configuration',visibility='evaluator')
def _c_tilt(rt,a):
    rt.tilt=[a['x_deg'],a['y_deg']]
    return {'ok':rt._level_ok()}


ERR_SCHEMA=obj({'span_rel':number('1',-.01,.01),'zero_kg':number('kg',-.01,.01),'internal_weight_rel':number('1',-.001,.001),
                'temp_offset_k':number('K',-10,10),'temp_gain':number('1',-.1,.1)},optional=('span_rel','zero_kg','internal_weight_rel','temp_offset_k','temp_gain'))
@term('sensor.configure_error',ERR_SCHEMA,obj({'errors':dict(type='object')}),['balance_sensor','temperature_sensor'],
      '评测者：传感器误差（称重量程/零点/内部砝码、温度偏置/增益），默认0（理想）；调整按手册流程修正',['sensor.errors','balance.indicated_kg',
      'thermal.indicated_temperature_k'],{'span_rel':1e-4},kind='configuration',visibility='evaluator')
def _c_errors(rt,a):
    rt.sensor.update(a)
    return {'errors':dict(rt.sensor)}


# ---------------------------------------------------------------- results and display values
def _value(mode,ww_g,w_g):
    """Display value of a weight pair (p.39-42): WW start (wet) weight, W current weight, grams."""
    if ww_g<=0: return 0.0
    lost=ww_g-w_g
    v={'%MC':lost/ww_g*100,'%DC':w_g/ww_g*100,'%AM':(lost/w_g*100 if w_g>0 else 999.99),'%AD':(ww_g/w_g*100 if w_g>0 else 999.99),'g':w_g,
       'g/kg MC':lost/ww_g*1000,'g/kg DC':w_g/ww_g*1000,'-%MC':-lost/ww_g*100}[mode]
    if mode in ATRO: v=max(-999.99,min(999.99,v))
    return v

def _decimals(p):
    if p['free_factor'] and p['display_mode']!='g': return len(p['format'])-2 if '.' in p['format'] else 0
    high=p['resolution']=='High' and p['display_mode'] not in ATRO
    return {'g':4 if high else 3,'g/kg MC':2 if high else 1,'g/kg DC':2 if high else 1}.get(p['display_mode'],3 if high else 2)

def _display(p,ww_g,w_g):
    v=_value(p['display_mode'],ww_g,w_g)
    if p['free_factor'] and p['display_mode']!='g': v=v*p['factor']+p['offset']
    n=_decimals(p)
    return float(_fmt_dec(v,n)),_fmt_dec(v,n)+' '+p['display_mode']+('*' if p['free_factor'] and p['display_mode']!='g' else '')

def _status(p,v):
    if not p['limits']: return 'none'
    if (p['t2_plus'] is not None and v>p['t2_plus']) or (p['t2_minus'] is not None and v<p['t2_minus']): return 'failed'
    if (p['t1_plus'] is not None and v>p['t1_plus']) or (p['t1_minus'] is not None and v<p['t1_minus']): return 'warned'
    return 'passed'

def _interp(curve,t):
    """Mass (kg) of a curve [(t, m)] at time t (linear between records)."""
    ts=[c[0] for c in curve]; i=bisect.bisect_left(ts,t)
    if i<=0: return curve[0][1]
    if i>=len(curve): return curve[-1][1]
    (t0,m0),(t1,m1)=curve[i-1],curve[i]
    return m0+(m1-m0)*((t-t0)/(t1-t0) if t1>t0 else 0.0)

def _soc_met(soc,st,curve,t_step,t,start_g,delay_s=0.0):
    """Is a switch-off criterion met at time t (step started at t_step)?  Timed: the time is over; 1-5 / free: the mean weight loss over the last
    dt is below dg (inactive during the first 30 s and the SOC delay, p.40-41)."""
    el=t-t_step
    if soc=='Timed': return el>=st['soc_time_s']-1e-9
    if soc in SOC_DT: dg,dt=1.0,SOC_DT[soc]
    elif soc=='Free (mg / s)': dg,dt=st['soc_free_mg'],float(st['soc_free_mg_dt'])
    else: dg,dt=st['soc_free_pct']/100.0*start_g*1000.0,float(st['soc_free_pct_dt'])
    if el<max(30.0,dt)+delay_s-1e-9: return False
    return (_interp(curve,t-dt)-_interp(curve,t))*1e6<dg-1e-9

def _apply_auto_delete(dev):
    s=dev['settings']
    if s['results.auto_delete']:
        lim=s['results.limit']
        if len(dev['results'])>lim: dev['results']=dev['results'][len(dev['results'])-lim:]


def _finish_run(rt,aborted,save=True,predicted_only=False,open_lid=True):
    """End of a terminal measurement: the result (display mode, free factor, limits status), the chamber opens (automatic mode), QuickPredict
    reference curves, results journal (auto-delete)."""
    run=rt.run; dev=rt.dev; w=dev['work']; p=run['params']
    ww=run['start_net_kg']*1000.0; dw=(rt.curve[-1]['mass_kg'] if rt.curve else run['start_net_kg'])*1000.0
    val,text=_display(p,ww,dw); stat=_status(p,val)
    pred=run.get('saved_prediction')
    if predicted_only and pred: val,text,stat=pred['value'],pred['text'],pred['status']
    curve=[[round(c['time_s'],3),c['mass_kg']] for c in rt.curve]
    step=max(1,len(curve)//200); short=curve[::step]+([curve[-1]] if curve and (len(curve)-1)%step else [])
    res=dict(id=dev['next_result'],method_id=run['method_id'],method_name=run['name'],version=run['version'],time=run['started'],
             duration_s=rt.elapsed_s,start_g=_round_to(ww,0.0001),dry_g=_round_to(dw,0.0001),value=val,text=text,unit=p['display_mode'],status=stat,
             aborted=aborted,test=run['test'],note=w['note'] if w else '',ids=dict(w['ids']) if w else {},predicted=pred,
             soc_points={k:v for k,v in run['soc_hits'].items()},curve=short,user=dev['current_user'] or '-')
    dev['next_result']+=1
    if run.get('purpose'):
        _purpose_finish(rt,run,res,aborted)
        if w is not None: w.update(stage='done',result_id=None,last=res)
        if open_lid: rt.lid_m=.1085
        rt.run=None
        return res
    m=_method(dev,run['method_id'])
    if run['test']:
        dev['test_result']=dict(method_id=run['method_id'],start_g=ww,curve=[[c['time_s'],c['mass_kg']] for c in rt.curve])
    if m is not None and m['params']['qp'] and m.get('qp_recording') and not aborted and not run['test']:
        lo=_value('%MC',ww,dw)/100.0; t=rt.elapsed_s
        t63=next((c['time_s'] for c in rt.curve if (ww-c['mass_kg']*1000.0)>=0.632*(ww-dw)),t)
        m['qp_refs'].append(dict(final=val,loss=lo,tau_s=max(t63,1.0)))
        if len(m['qp_refs'])>=6:                                                       # p.59: six reference curves -> prediction knowledge
            fin=[r['final'] for r in m['qp_refs']]; mean=sum(fin)/6.; sd=math.sqrt(sum((x-mean)**2 for x in fin)/5.)
            m['qp_knowledge']=dict(tau_s=sum(r['tau_s'] for r in m['qp_refs'])/6.,sd=sd); m['qp_recording']=False
            m['params']['qp_usage']='Active'; m['version']+=1
    s=dev['settings']
    stored=save and (not s['results.auto_delete'] or s['results.limit']>0)              # p.107: result limit 0 = Zero Results
    if stored:
        dev['results'].append(res); _apply_auto_delete(dev)
    if w is not None:
        w.update(stage='done',result_id=res['id'] if stored else None,last=res)
    if open_lid:
        rt.lid_m=.1085                                                                  # the chamber opens after the measurement (p.32)
    if p['preheating']=='Standby' and m is not None: _preheat_on(rt,m)
    rt.run=None
    return res


def _prediction(rt):
    """QuickPredict (p.64-66): after 2 min, the final result predicted from the current loss and the method's reference time constant
    (prediction knowledge), with its uncertainty from the spread of the six reference results."""
    run=rt.run; m=_method(rt.dev,run['method_id'])
    if m is None or run.get('purpose') or not run['params']['qp'] or run['params']['qp_usage']!='Active' or not m['qp_knowledge'] or run['test']: return None
    if rt.elapsed_s<120.0: return dict(state='pending',text='Prediction pending (1)')
    k=m['qp_knowledge']; ww=run['start_net_kg']*1000.0; w=rt.curve[-1]['mass_kg']*1000.0
    frac=1-math.exp(-rt.elapsed_s/k['tau_s'])
    if frac<0.05: return dict(state='pending',text='Prediction pending (x)')
    dw=ww-(ww-w)/frac; p=run['params']
    val,text=_display(p,ww,dw); unc=max(k['sd'],10**-_decimals(p))
    return dict(state='ok',value=val,uncertainty=float(_fmt_dec(unc,_decimals(p))),text=f'{_fmt_dec(val,_decimals(p))} ± {_fmt_dec(unc,_decimals(p))} {p["display_mode"]}',
                status=_status(p,val))


# <<< CHEM-002 HX204 terminal

_PANELS=weakref.WeakKeyDictionary()                                                    # one PanelUI per runtime, kept out of __dict__

RUN_GUARDED=('acquisition.stop','measurement.stop','measurement.qp_action','power.set','lid.set_position')   # another agent's drying: refused for agents
class MtHx204Runtime:
    def __init__(self,instance_id='hx204_01'):
        self.instance_id=instance_id; self.time_s=0.; self.revision=0
        self.powered=False; self.target_k=378.15; self.temperature_k=298.15
        self.lid_m=0.; self.mode='standard'; self.duration_s=600.; self.basis='wet'
        self.phase='idle'; self.elapsed_s=0.; self.residue_kg=0.
        self.initial_species={}; self.remaining={}; self.evaporated={}; self.curve=[]
        self.operation_id=None; self._operation_count=0
        self._principals={'__public_agent__':'agent'}; self._requests={}; self._owners={}
        self._caller=None                                    # principal of the session call in progress (direct or panel gesture)
        self.start_kg=None                                   # indicated net weight at the start of the drying: reference of the loss
        self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances']
        # terminal layer (1.1.0)
        self.hx_faults=[]                                                            # evaluator error causes of manual 9.1 (1.3.0)
        self.dev=_new_device(); self.run=None; self.pan_load_kg=0.; self.tare_kg=0.; self.kit_installed=False; self.tilt=[0.,0.]; self.smartcal=None
        self.sensor=dict(span_rel=0.,zero_kg=0.,internal_weight_rel=0.,temp_offset_k=0.,temp_gain=0.)
        self.cal=dict(span=1.,temp_a=1.,temp_b=0.)
        self.panel_capture_dir=None

    @property
    def panel(self):
        p=_PANELS.get(self)
        if p is None:
            p=_PANELS[self]=_panel_ui.PanelUI(PANEL_SPEC,self,hooks=PANEL_HOOKS)
        return p

    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','evaluator','debug'): raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals: raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility

    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals: raise ActionRejected('ACCESS_DENIED')
        return MtHx204Session(self,principal_id)

    # ---- sensors (ideal by default: errors 0, corrections identity)
    def _level_ok(self): return math.hypot(*self.tilt)<=0.1                             # assumption: 0.1 deg
    def _raw_c(self,true_c): return true_c*(1+self.sensor['temp_gain'])+self.sensor['temp_offset_k']
    def _sensor_c(self): return self.cal['temp_a']*self._raw_c(self.temperature_k-273.15)+self.cal['temp_b']
    def _control_k(self,target_c):
        """True temperature at which the corrected sensor reading equals the target (the heater controls on the sensor)."""
        raw=(target_c-self.cal['temp_b'])/self.cal['temp_a']
        return (raw-self.sensor['temp_offset_k'])/(1+self.sensor['temp_gain'])+273.15

    def _indicated_kg(self): return round((_corrected_kg(self)-self.tare_kg)/1e-7)*1e-7

    def _values(self):
        m=self.residue_kg+sum(self.remaining.values())
        return {'power.enabled':self.powered,'thermal.target_k':self.target_k,
          'thermal.temperature_k':self.temperature_k,'thermal.indicated_temperature_k':round(self._sensor_c(),1)+273.15,
          'lid.position_m':self.lid_m,'method.mode':self.mode,'method.duration_s':self.duration_s,'method.basis':self.basis,
          'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'balance.indicated_kg':self._indicated_kg(),
          'sample.initial_kg':self.residue_kg+sum(self.initial_species.values()),'sample.remaining_kg':m,
          'sample.evaporated_kg':sum(self.evaporated.values())}

    def _terminal_values(self):
        dev=self.dev; u=_user(dev)
        users=dict(current=dev['current_user'],locked=bool(dev.get('locked')),
                   users=[dict(user_name=x['user_name'],full_name=x['full_name'],description=x['description'],active=x['active'],group=x['group'],
                               has_password=x['password'] is not None,shortcuts=copy.deepcopy(x['shortcuts']),methods=copy.deepcopy(x['methods']),
                               modified=x['modified']) for x in dev['users']],
                   groups=[dict(group_name=g['group_name'],description=g['description'],builtin=g['builtin'],rights=dict(g['rights']),modified=g['modified'])
                           for g in dev['groups']],rights=sorted(_rights(dev)),setup_done=dev['setup_done'])
        w=dev['work']; live=None
        if w is not None:
            m=_work_method(self,w); p=(self.run or {}).get('params') or (m['params'] if m else None)
            net_g=_net_kg(self)*1000.0
            live=dict(net_g=_read_g(self,p['resolution'] if p else 'Standard'),temperature_c=round(self._sensor_c(),1),elapsed_s=self.elapsed_s,
                      value=None,text=None,status=None,step=None,prediction=None,start_weight_ok=None)
            f=_weigh_fault(self); live['weigh_fault']=HX_FAULTS[f][1] if f else None
            live['plot_span']=max(60.0,self.elapsed_s*1.05)
            if p and p['start_weight']:
                lo,hi=p['start_weight_g']*(1-p['start_weight_tol']/100.),p['start_weight_g']*(1+p['start_weight_tol']/100.)
                live['start_weight_ok']=lo-1e-9<=net_g<=hi+1e-9; live['start_weight_range']=[lo,hi]
                full=hi*1.25                                                                   # weighing-in aid bar: 0 .. 125 % of the upper limit
                live.update(aid_fraction=max(0.0,min(1.0,net_g/full)),aid_lo=lo/full,aid_hi=hi/full)
            if self.run is not None:
                ww=self.run['start_net_kg']*1000.0; val,text=_display(p,ww,self.curve[-1]['mass_kg']*1000.0 if self.curve else net_g)
                live.update(value=val,text=text,status=_status(p,val),step=self.run['step']+1 if p['program']=='Steps' else None,
                            prediction=copy.deepcopy(self.run.get('prediction')),start_g=_round_to(ww,0.001),
                            curve=[[c['time_s'],_value(p['display_mode'],ww,c['mass_kg']*1000.0)] for c in self.curve[::max(1,len(self.curve)//150)]])
            elif w.get('last'):
                r=w['last']; live.update(value=r['value'],text=r['text'],status=r['status'],start_g=r['start_g'],
                                         curve=[[t,_value(r['unit'],r['start_g'],mm*1000.0)] for t,mm in r['curve']])
        pre=dev.get('preheat')
        work=None if w is None else dict(method_id=w['method_id'],test=w['test'],test_time_s=w['test_time_s'],stage=w['stage'],note=w['note'],
                                          operation_id=self.operation_id if w['stage'] in ('running','done') else None,
                                          result_id=w['result_id'],ids=dict(w['ids']),purpose=w.get('purpose'),
                                          preheat=None if pre is None else dict(kind=pre['kind'],target_c=pre['target_c'],
                                                                                reached=abs(self._sensor_c()-pre['target_c'])<=1.0))
        d=dev['draft']
        draft=None if d is None else dict(method_id=d['id'],name=d['name'],params=copy.deepcopy(d['params']),qp_refs=len(d['qp_refs']),
                                          qp_knowledge=d['qp_knowledge'] is not None,qp_issues=_qp_issues(d['params']))
        proc=dev['proc'] or dev['last_proc']
        return {
            'device.clock':_now_dt(self).isoformat(timespec='seconds'),'device.settings':copy.deepcopy(dev['settings']),
            'users.state':users,'user.preferences':copy.deepcopy(u['prefs']) if u else _default_prefs(dev),
            'methods.list':[dict(id=m['id'],name=m['name'],version=m['version'],params=copy.deepcopy(m['params']),qp_refs=len(m['qp_refs']),
                                 qp_knowledge=m['qp_knowledge'] is not None,qp_recording=bool(m.get('qp_recording'))) for m in dev['methods']],
            'method.draft':draft,'work.state':work,'measurement.live':live,'results.list':copy.deepcopy(dev['results']),'fault.injected':list(self.hx_faults),
            'tests.history':copy.deepcopy(dev['history']),'procedure.state':None if proc is None else {k:copy.deepcopy(v) for k,v in proc.items()
                                                                                                         if k not in ('load_since','unload_since','phase_start_s')},
            'identifications':list(dev['ids']),'level.state':dict(x_deg=self.tilt[0],y_deg=self.tilt[1],ok=self._level_ok()),
            'balance.tare_kg':self.tare_kg,'temperature_kit.installed':self.kit_installed,'balance.pan_load_kg':self.pan_load_kg,
            'sensor.errors':dict(self.sensor),'sensor.corrections':dict(self.cal),
            'wizard.state':copy.deepcopy(dev['wizard']),'assistant.result':copy.deepcopy(dev['assistant']),
            'qp_wizard.state':None if dev['qp_wizard'] is None else dict({k:copy.deepcopy(v) for k,v in dev['qp_wizard'].items() if k not in ('saved',)},
                                                                         current=dev['qp_wizard']['schedule'][min(dev['qp_wizard']['index'],5)]),
            'smartcal.state':_smartcal_view(self),'smartcal.sample':copy.deepcopy(self.smartcal)}

    def read(self,visibility='agent'):
        vals=dict(self._values(),**self._terminal_values())
        return copy.deepcopy({k:v for k,v in vals.items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})

    def describe(self): return self.session().describe(instance_id=self.instance_id)

    def debug_snapshot(self):
        return dict(state=self.read('evaluator'), species=copy.deepcopy(self.remaining), evaporated=copy.deepcopy(self.evaporated))

    def _check_caller(self,capability,a):
        """An agent may not stop or interrupt another agent's drying (direct call or panel gesture alike: the session sets _caller around both)."""
        who=self._caller
        if who is None or self._principals.get(who)!='agent' or self.phase!='running' or capability not in RUN_GUARDED: return
        owner=self._owners.get(self.operation_id)
        if owner is None or owner==who: return
        if capability=='power.set' and a['enabled']: return
        if capability=='lid.set_position' and a['position_m']<=1e-6: return
        raise ActionRejected('ACCESS_DENIED')

    def _measure(self):
        v=self._values(); initial=self.start_kg if self.start_kg is not None else v['balance.indicated_kg']; m=v['balance.indicated_kg']
        loss=max(0.,min(1.,(initial-m)/initial)) if initial else 0.
        ratio=loss if self.basis=='wet' else (loss*initial/m if m>0 else None)
        return dict(phase=self.phase,mass_kg=m,loss_fraction=loss,reported_ratio=ratio,basis=self.basis,
          curve=copy.deepcopy(self.curve),template_source=TEMPLATE_REL,evidence='synthetic',interpretation='loss_on_drying_not_water_specific')

    def invoke(self,capability,**a):
        if capability not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a)
        self._check_caller(capability,a)
        if capability in TERMINAL or capability in PANEL_CAPS:
            return self._terminal(capability,a)
        if capability in ('method.configure','thermal.set_temperature','sample.configure') and self.phase=='running': raise ActionRejected('BUSY')
        if capability=='thermal.set_temperature':
            if not math.isclose(a['temperature_k']-273.15,round(a['temperature_k']-273.15),abs_tol=1e-7): raise ActionRejected('INVALID_ARGUMENT')
            self.target_k=a['temperature_k']; result=dict(a)
        elif capability=='power.set':
            was=self.powered; self.powered=a['enabled']
            if not self.powered and self.phase=='running': self.phase='cancelled'
            self._power_changed(was)
            result=dict(a)
        elif capability=='method.configure':
            self.mode=a['mode']; self.duration_s=a['duration_s']; self.basis=a['basis']; result=dict(a)
        elif capability=='lid.set_position':
            manual=self.run is not None and self.run['params']['starting_mode']=='Manual'
            if not self.powered or (self.phase=='running' and a['position_m']>0 and not manual): raise ActionRejected('PRECONDITION_FAILED')
            self.lid_m=a['position_m']; result=dict(a)
            w=self.dev['work']
            if w is not None and w.get('armed') and self.lid_m<=1e-6:                        # manual mode: closing the chamber starts the drying
                _begin_run(self,_method(self.dev,w['method_id']),w,w['start_net_kg'])
        elif capability=='sample.configure':
            species=a['species_kg']; residue=a['residue_kg']
            if self.lid_m<.1: raise ActionRejected('PRECONDITION_FAILED')
            if not set(species)<=self.templates.keys() or not .0001<=residue+sum(species.values())<=.201: raise ActionRejected('INVALID_ARGUMENT')
            self.initial_species=dict(species); self.remaining=dict(species); self.evaporated={k:0. for k in species}
            self.residue_kg=residue; self.curve=[]; self.phase='idle'; self.elapsed_s=0.; self.operation_id=None; self.start_kg=None
            result={'configured':True}
        elif capability=='acquisition.start':
            if self.phase=='running': raise ActionRejected('BUSY')
            if not self.powered or self.lid_m>1e-6 or self._values()['sample.remaining_kg']<.0001 or self.phase!='idle': raise ActionRejected('PRECONDITION_FAILED')
            self._operation_count+=1; self.operation_id=f'{self.instance_id}:drying:{self._operation_count}'
            self.phase='running'; self.elapsed_s=0.; self.curve=[]; self.run=None; self.start_kg=self._indicated_kg(); self._record()
            result={'phase':self.phase,'operation_id':self.operation_id}
        elif capability=='acquisition.stop':
            if self.phase=='running':
                self.phase='cancelled'
                if self.run is not None: _finish_run(self,aborted=True,save=True)
            result={'phase':self.phase}
        else: return self._measure()
        self.revision+=1
        return copy.deepcopy(result)

    def _power_changed(self,was):
        """ON/OFF (standby, p.23): switching off closes the work screen and the method editor; switching on activates the default startup user
        (else the login dialog) and runs FACT."""
        dev=self.dev
        if was and not self.powered:
            if self.run is not None: _finish_run(self,aborted=True,save=True,open_lid=False)
            dev['work']=None; dev['draft']=None; dev['proc']=None; dev['preheat']=None; dev['locked']=False
        if not was and self.powered:
            dev['power_epoch']+=1; s=dev['settings']
            start=s['policy.startup_user'] if s['policy.startup'] else None
            dev['current_user']=start if start and _user(dev,start) and _user(dev,start)['active'] else None
            if s['fact']: self._fact()

    def _fact(self):
        """FACT (p.81, p.103): internal adjustment after switching on and at a fixed interval (assumption: every 4 h while idle)."""
        if self._gross_ok_for_fact():
            _internal_adjust(self,'FACT'); self.dev['last_fact_s']=self.time_s

    def _gross_ok_for_fact(self): return _gross_kg(self)<1e-6 and self.phase!='running' and self.dev['proc'] is None

    def _terminal(self,capability,a):
        """Terminal commands and panel gestures: transactional (a refusal changes nothing of the instrument)."""
        keep=('templates','_principals','_requests','_owners')
        old=copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in keep})
        try:
            r=TERMINAL[capability](self,a) if capability in TERMINAL else self._panel_cmd(capability,a)
        except Exception:
            for k in list(self.__dict__):
                if k not in keep: del self.__dict__[k]
            self.__dict__.update(old); raise
        if CAPS[capability][4]!='query': self.revision+=1
        if capability in TERMINAL: self.dev['last_activity_s']=self.time_s
        return copy.deepcopy(r)

    def _record(self):
        v=self._values(); m=v['balance.indicated_kg']; initial=self.start_kg if self.start_kg is not None else m   # the device's own start weight
        self.curve.append(dict(time_s=self.elapsed_s,mass_kg=m,loss_fraction=max(0,min(1,(initial-m)/initial)) if initial else 0.))

    def _program_target_k(self):
        """Heater target of a terminal measurement (p.34-38): Standard; Rapid 40 % above for 3 min (at most 230 degC); Gentle ramp from 50 degC over
        the ramp time; Steps the current step's temperature; the heater controls on the (corrected) sensor reading."""
        run=self.run; p=run['params']; t=self.elapsed_s
        if run.get('purpose')=='tassist':                                            # temperature assistant: ramp to the accepted maximum, then hold
            ta=run['tassist']; return self._control_k(min(ta['max_c'],TA_START_C+TA_RATE_C_MIN*t/60.0))
        if p['program']=='Steps': c=run['steps'][run['step']]['temperature']
        else:
            c=p['temperature']
            if p['program']=='Rapid' and t<180.0: c=min(230.0,c*1.4)
            if p['program']=='Gentle' and p['ramp_s']>0:
                if run.get('ramp_t0') is None:
                    if self._sensor_c()>=50.0-1e-9: run['ramp_t0']=t
                    c=max(50.0,min(c,50.0))
                else:
                    c=50.0+(c-50.0)*min(1.0,(t-run['ramp_t0'])/p['ramp_s'])
        return self._control_k(c)

    def _idle_target_k(self):
        qw=self.dev.get('qp_wizard')                                                 # QuickPredict Wizard: warm the instrument up for a warm start
        if self.powered and qw is not None and qw['stage'] in ('conditioning','ready') and qw['schedule'][qw['index']]=='warm':
            d=self.dev['draft']; return self._control_k(float(d['params']['temperature'] if d else 105))
        pre=self.dev.get('preheat')
        if self.powered and pre is not None and self.dev['proc'] is None: return self._control_k(pre['target_c'])
        pr=self.dev['proc']
        if self.powered and pr is not None and pr['stage']=='heating': return self._control_k(pr['plateaus'][pr['index']][0])
        return 298.15

    def _check_soc(self):
        """Switch-off criteria of a terminal measurement after a record: next step or end (max 480 min); a test measurement records when each
        criterion 1-5 is reached and ends when all are (or at the test time, p.52-53)."""
        run=self.run; p=run['params']; t=self.elapsed_s; start_g=run['start_net_kg']*1000.0
        curve=[(c['time_s'],c['mass_kg']) for c in self.curve]
        if run.get('purpose')=='tassist':                                            # p.54: the temperature at which the reference value is reached
            ta=run['tassist']; v=_value(p['display_mode'],start_g,curve[-1][1]*1000.0); tol=_ta_tol(ta['reference'])
            if ta['hit_c'] is None and v>=ta['reference']-tol: ta['hit_c']=self._sensor_c()
            if (t>=ta['ramp_end_s']-1e-9 and _soc_met('3',{},curve,ta['ramp_end_s'],t,start_g)) or t>=28800-1e-9:
                self.phase='succeeded'; _finish_run(self,aborted=False)
            return
        if run['test']:
            for soc in SOC_DT:
                if soc not in run['soc_hits'] and _soc_met(soc,{},curve,0.0,t,start_g):
                    m=curve[-1][1]*1000.0; run['soc_hits'][soc]=dict(time_s=t,value=_display(p,start_g,m)[0])
            done=len(run['soc_hits'])==5 or (run['test_time_s'] is not None and t>=run['test_time_s']-1e-9) or t>=28800-1e-9
            if done: self.phase='succeeded'; _finish_run(self,aborted=False)
            return
        st=run['steps'][run['step']]
        delay=p['soc_delay_min']*60.0 if (p['program']=='Standard' and p['soc_delay']) else 0.0
        if _soc_met(st['soc'],st,curve,run['step_start_s'],t,start_g,delay) or t>=28800-1e-9:
            if run['step']+1<len(run['steps']) and t<28800-1e-9:
                run['step']+=1; run['step_start_s']=t
            else:
                self.phase='succeeded'; _finish_run(self,aborted=False)

    def step(self,dt_s):
        if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0: raise ValueError('dt_s must be finite and positive')
        left=float(dt_s)
        while left>1e-10:
            active=self.phase=='running'; h=min(left,1.)
            terminal=active and self.run is not None
            if active and not terminal: h=min(h,max(0.,self.duration_s-self.elapsed_s))
            if terminal:
                st=self.run['steps'][self.run['step']]
                if st['soc']=='Timed' and not self.run['test']: h=min(h,max(0.,self.run['step_start_s']+st['soc_time_s']-self.elapsed_s))
                if self.run['test'] and self.run['test_time_s'] is not None: h=min(h,max(0.,self.run['test_time_s']-self.elapsed_s))
                h=min(h,max(0.,28800-self.elapsed_s))
                if h<=1e-10: self._check_soc(); continue
            if h<=1e-10: self.phase='succeeded'; continue
            interrupted=terminal and self.lid_m>1e-6                                     # manual mode: an open chamber interrupts the heating
            if terminal and not interrupted:
                target=self._program_target_k(); tau=45.
            elif active and not terminal:                                                # 1.0.0 run: the heater also controls on the corrected sensor
                target=self._control_k(self.target_k-273.15)
                tau={'standard':45.,'rapid':20.,'gentle':100.,'steps':45.}[self.mode]
                if self.mode=='steps' and self.elapsed_s<self.duration_s/2: target=self._control_k((298.15+self.target_k)/2-273.15)
            else:
                target=self._idle_target_k(); tau=45.
            self.temperature_k=target+(self.temperature_k-target)*math.exp(-h/tau)
            if active and self.smartcal is not None:
                sc=self.smartcal; cap=sc['mass_kg']*max(0.0,_sc_mc(self.temperature_k-273.15)+SC_RH_COEF*(sc['room_rh_pct']-SC_RH_REF))/100.0
                if cap>sc['released_kg']: sc['released_kg']=cap-(cap-sc['released_kg'])*math.exp(-h/SC_TAU_S)
            if active:
                for sid,m in self.remaining.items():
                    k=.002*math.exp(max(-15,min(8,(self.temperature_k-self.templates[sid]['value'])/30)))
                    rem=m*math.exp(-k*h); self.remaining[sid]=rem; self.evaporated[sid]+=m-rem
                self.elapsed_s+=h; self._record()
                if terminal: self._check_soc()
                elif self.elapsed_s>=self.duration_s-1e-9: self.phase='succeeded'
                if self.run is not None and self.phase=='running': self.run['prediction']=_prediction(self)
            self.time_s+=h; left-=h
            self._tick()
        self.revision+=1
        return self.read('evaluator')

    def _tick(self):
        """Clock-driven terminal state: procedures, preheating time-outs, FACT interval, auto lock-out (p.106)."""
        _proc_step(self); _qp_wizard_tick(self); dev=self.dev
        pre=dev.get('preheat')
        if pre is not None:
            hm=_parse_time(pre['turnoff']) if pre.get('turnoff') else None
            now=_now_dt(self)
            if self.time_s>=pre['until_s']-1e-9 or (hm and (now.hour,now.minute)==hm): dev['preheat']=None
        if self.powered and dev['settings']['fact'] and dev['last_fact_s'] is not None and self.time_s-dev['last_fact_s']>=4*3600 and self._gross_ok_for_fact():
            self._fact()
        s=dev['settings']
        if self.powered and s['policy.lockout'] and dev['current_user'] and self.phase!='running' and \
                self.time_s-dev['last_activity_s']>=s['policy.lockout_min']*60-1e-9:
            dev['locked']=True

    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        """What the terminal shows: the agent slots plus the power-up count, the methods the user may run, the selected method and result."""
        out=self.read('agent'); dev=self.dev
        w=dev['work']; m=_work_method(self,w) if w else None
        u=_user(dev); sc={x['method_id'] for x in (u['shortcuts'] if u else [])}
        out.update({'host.powered':self.powered,'host.power_epoch':dev['power_epoch'],
                    'host.allowed_methods':[dict(id=x['id'],name=x['name'],shortcut=x['id'] in sc) for x in _allowed_methods(dev)],
                    'host.work_method':copy.deepcopy(m) if m else None,'host.ids_items':[dict(id=x,name=x) for x in dev['ids']],
                    'host.ids_text':', '.join(f"{k}={w['ids'].get(k,'')}" for k in dev['ids']) if w else '',
                    'host.own_password':bool(u and u['password'] is not None),
                    'host.has_test_result':bool(dev['test_result'] and dev['draft'] and dev['test_result']['method_id']==dev['draft']['id']),
                    'host.tassist_max_c':int(min(230,(dev['draft']['params']['temperature'] if dev['draft'] else 110)+50)),
                    'host.smartcal_methods':[dict(test=i,name=_smartcal_method(self,i)['name'],temperature=_sc_temp(dev['settings'],i),
                                                  limits=_sc_limits(dev['settings'],i)) for i in range(1,7) if dev['settings'][f'sc.m{i}.on']],
                    'host.wizard_soc':[dict(soc=k,time_s=v['time_s'],value=v['value'],recommended=k==(dev['wizard'] or {}).get('test',{}).get('recommended'))
                                       for k,v in sorted(((dev['wizard'] or {}).get('test') or {}).get('hits',{}).items())]})
        out.update(self._panel_lists())
        return out

    def _panel_lists(self):
        """List views of the result, history, method-selection and procedure screens (computed from the slots, newest first)."""
        dev=self.dev; now=_now_dt(self); today=now.date().isoformat(); week=(now.date()-datetime.timedelta(days=now.weekday())).isoformat()
        views,series,versions,curves,spans,soc={},{},{},{},{},{}
        for m in dev['methods']:
            rs=[r for r in dev['results'] if r['method_id']==m['id']]
            rows=[]
            for i,r in enumerate(reversed(rs)):
                rows.append(dict(id=r['id'],time=r['time'],date=r['time'][:10],text=r['text'],status=r['status'],aborted=r['aborted'],test=r['test'],
                                 predicted=r['predicted'] is not None,version=r['version'],rank=i,today=r['time'][:10]==today,this_week=r['time'][:10]>=week))
            views[str(m['id'])]=rows
            pts=[[i+1,r['value']] for i,r in enumerate(x for x in rs if not x['test'])]
            series[str(m['id'])]=pts; spans[str(m['id'])]=max(10,len(pts))
            versions[str(m['id'])]=[dict(v=v,text=f'Version {v}') for v in sorted({r['version'] for r in rs})]
        for r in dev['results']:
            curves[str(r['id'])]=[[t,_value(r['unit'],r['start_g'],mm*1000.0)] for t,mm in r['curve']]
            spans['r'+str(r['id'])]=max(60.0,r['duration_s'])
            soc[str(r['id'])]=', '.join(f"{k}: {v['time_s']:.0f} s {v['value']}" for k,v in sorted(r['soc_points'].items())) or '-'
        hist={}
        for k,lst in dev['history'].items():
            hist[k]=[dict(e,detail=_history_text(e)) for e in reversed(lst)]
        msel={}
        for u in dev['users']:
            ids=u['methods']; rows=[]
            for m in dev['methods']:
                on=ids is None or m['id'] in ids
                cur=[x['id'] for x in dev['methods']] if ids is None else list(ids)
                rows.append(dict(id=m['id'],name=m['name'],enabled=on,toggled=[x for x in cur if x!=m['id']] if on else cur+[m['id']]))
            msel[u['user_name']]=rows
        pr=dev['proc'] or dev['last_proc']
        return {'host.result_methods':[dict(id=m['id'],name=m['name'],count=len(views[str(m['id'])])) for m in dev['methods']],
                'host.results_view':views,'host.result_series':series,'host.result_versions':versions,'host.result_curve':curves,
                'host.result_span':{k[1:]:v for k,v in spans.items() if k.startswith('r')},'host.result_series_span':{k:v for k,v in spans.items() if not k.startswith('r')},
                'host.result_series_text':{k:(f'{len(v)} results, last {v[-1][1]}' if v else 'No results') for k,v in series.items()},
                'host.result_soc_text':soc,'host.history':hist,'host.method_selection':msel,
                'host.proc_detail':_history_text(pr['result']) if pr and pr.get('result') else ''}

    def panel_invoke(self,capability_id,arguments):
        try:
            validate(CAPS[capability_id][0],arguments)
            self._check_caller(capability_id,arguments)
            if capability_id in TERMINAL:
                return self._panel_terminal(capability_id,arguments)
            return self.invoke(capability_id,**arguments)
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None

    def _panel_terminal(self,capability,a):
        """A terminal command from a panel gesture: the same transactional path as a direct call, without rolling back the panel."""
        keep=('templates','_principals','_requests','_owners')
        old=copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in keep})
        try:
            r=TERMINAL[capability](self,a)
        except Exception:
            for k in list(self.__dict__):
                if k not in keep: del self.__dict__[k]
            self.__dict__.update(old); raise
        self.dev['last_activity_s']=self.time_s
        return r

    def panel_time(self): return self.time_s

    def _panel_cmd(self,capability,a):
        if capability in ('panel.press','panel.touch'): self.dev['last_activity_s']=self.time_s   # a touch is user activity (auto lock-out)
        try:
            if capability=='panel.press': return dict(accepted=bool(self.panel.press(a['input_id'],float(a.get('duration_s',.1)))))
            if capability=='panel.touch':
                if ('to_x' in a)!=('to_y' in a): raise ActionRejected('INVALID_ARGUMENT')
                return dict(accepted=bool(self.panel.touch(a['display_id'],a['x'],a['y'],float(a.get('duration_s',.1)),
                                                             (a['to_x'],a['to_y']) if 'to_x' in a else None)))
            if capability=='panel.capture':
                data,w,h=self.panel.png_bytes(a['display_id']); sha=hashlib.sha256(data).hexdigest()
                d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id
                d.mkdir(parents=True,exist_ok=True); path=d/f"{a['display_id']}_{sha[:16]}.png"
                if not path.exists(): path.write_bytes(data)
                return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
            return dict(frame=self.panel.frame())
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None


class MtHx204Session:
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
        meta=export_mt_hx204_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,
          capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},
          state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})

    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id); h=self._host; vals=h.read(self._role); ids=list(vals) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids): raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in vals for k in ids): raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},
          observations={k:dict(value=copy.deepcopy(vals[k]),unit=SLOTS[k][1],quality='estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})

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
        op=result.get('operation_id') if capability_id in ('acquisition.start','measurement.start') else None
        if op: h._owners[op]=self._principal
        elif h.operation_id and h.operation_id!=before: h._owners[h.operation_id]=self._principal   # a drying started by a panel gesture or by closing the chamber
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


# ---------------------------------------------------------------- terminal state slots
ANY_OBJ={'type':'object'}
def _slot(k,schema,unit,origin,vis,components,write=None,meaning=None):
    SLOTS[k]=(schema,unit,origin,vis,components,write); SLOT_ZH[k]=meaning
SLOT_ZH={}
NULL_OBJ={'type':['object','null']}
_slot('device.clock',TEXT,'1','setpoint','agent',['screen'],None,'终端时钟（ISO本地时间；clock.set设定，随仿真时间走）')
_slot('device.settings',ANY_OBJ,'1','setpoint','agent',['screen','terminal'],None,'仪器设置（地区、外设、网络、默认、共享目录、公司、标识、打印导出、质量管理、账户策略、结果管理）')
_slot('users.state',ANY_OBJ,'1','setpoint','agent',['screen'],None,'用户管理：当前用户、锁屏、用户（无密码）、组与权限、快捷方式、方法选择、Setup wizard完成')
_slot('user.preferences',ANY_OBJ,'1','setpoint','agent',['screen'],None,'当前用户偏好：显示/键盘语言、亮度、配色、声音')
_slot('methods.list',dict(type='array',items=ANY_OBJ),'1','setpoint','agent',['terminal'],None,'方法列表（参数、版本、QuickPredict参考曲线数与预测知识）')
_slot('method.draft',NULL_OBJ,'1','setpoint','agent',['terminal'],None,'参数菜单中正在编辑、尚未保存的方法草稿')
_slot('work.state',NULL_OBJ,'1','derived','agent',['screen'],None,'工作屏：方法、阶段（去皮/加样/手动待关/干燥/完成）、注释、ID、预热、本次干燥的operation_id（干燥中与完成后；可poll/cancel）')
_slot('measurement.live',NULL_OBJ,'1','derived','agent',['screen','display_digits'],None,'工作屏数值：净重、温度、结果值与文字、状态、曲线、预测')
_slot('results.list',dict(type='array',items=ANY_OBJ),'1','derived','agent',['terminal'],None,'结果日志（方法与版本、时间、起始/干重、结果、状态、中止/测试、注释、ID、预测、曲线）')
_slot('tests.history',ANY_OBJ,'1','derived','agent',['terminal'],None,'测试/调整历史（每类最多50条）')
_slot('procedure.state',NULL_OBJ,'1','derived','agent',['screen'],None,'正在进行或刚结束的测试/调整：阶段、提示、读数与结果')
_slot('identifications',dict(type='array',items=TEXT,maxItems=MAX_IDS),'1','setpoint','agent',['terminal'],None,'标识名称（最多10个）')
_slot('level.state',ANY_OBJ,'1','derived','agent',['level_indicator','screen'],None,'倾角传感器：倾斜与是否水平')
_slot('balance.tare_kg',number('kg'),'kg','setpoint','agent',['balance_sensor'],None,'去皮值')
_slot('temperature_kit.installed',BOOL,'1','scene','agent',['chamber_floor'],None,'温度调整套件是否插入')
_slot('balance.pan_load_kg',number('kg',0),'kg','scene','evaluator',['pan_holder','sample_pan'],None,'盘架上样品以外物体的真实质量（评测者）')
_slot('sensor.errors',ANY_OBJ,'1','process_truth','evaluator',['balance_sensor','temperature_sensor'],None,'传感器误差参数（仿真用，默认0）')
_slot('sensor.corrections',ANY_OBJ,'1','process_truth','evaluator',['balance_sensor','temperature_sensor'],None,'调整存储的修正（量程系数、温度线性修正）')
_slot('wizard.state',NULL_OBJ,'1','derived','agent',['screen'],None,'Method Wizard：当前步骤、问卷答案、建议参数、参考值、温度助手与测试测量结果（推荐关断标准）')
_slot('assistant.result',NULL_OBJ,'1','derived','agent',['screen'],None,'最近一次温度助手结果：是否成功、建议干燥温度、最终值与提示')
_slot('qp_wizard.state',NULL_OBJ,'1','derived','agent',['screen','heater'],None,'QuickPredict Wizard：冷/热顺序、已记录参考曲线数、阶段（调温/就绪/测量/计算完成）、计时、上一条曲线评价')
_slot('smartcal.state',NULL_OBJ,'1','derived','agent',['screen'],None,'SmartCal工作屏：方法、温度、控制限、室温/湿度、批号、颗粒是否放入、结果（归一化%MCN与通过/失败）')
_slot('fault.injected',dict(type='array',items=TEXT,maxItems=4),'1','setpoint','evaluator',['balance_sensor'],None,'评测者注入且尚未消除的手册9.1错误原因（fault.inject，1.3.0）')
_slot('smartcal.sample',NULL_OBJ,'1','process_truth','evaluator',['sample_pan'],None,'盘上的SmartCal颗粒（质量、已释放水分、存放湿度；评测者）')


# ---------------------------------------------------------------- front panel: engine, spec and panel capabilities
def _panel_engine():
    try:
        from asset_runtime import _panel_ui as engine
        return engine
    except ImportError:
        import importlib.util,sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec); spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


PANEL_HOOKS={}
_panel_ui=_panel_engine()
_BUILD=os.environ.get('HX204_PANEL_BUILD')=='1'                                       # the spec builder imports the tables without the spec
PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS)) if (ASSET_DIR/'panel_ui.json').exists() and not _BUILD else None
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}
_PANEL_FX=sorted({e for c in (_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []) for e in CAPS[c][7]})
for _k,_d in PANEL_CAPS.items():
    CAPS[_k]=(_d['input_schema'],_d['output_schema'],_d['component_ids'],_d['function_zh'],_d['kind'],_d['visibility'],_d['example_arguments'],
              [] if _d['kind']=='query' else _PANEL_FX)


ERRORS_ZH={'UNKNOWN_INSTANCE':'实例不存在','UNKNOWN_CAPABILITY':'能力不存在','ACCESS_DENIED':'无权访问（评测者能力或真值）','INVALID_ARGUMENT':'参数类型/范围/键不符',
           'PRECONDITION_FAILED':'当前状态不允许','BUSY':'有测量、测试或调整在进行','REQUEST_CONFLICT':'同一request_id对应不同调用','STANDBY':'终端在待机',
           'LOGIN_REQUIRED':'未登录','PERMISSION_DENIED':'当前用户组没有该访问权限','SETUP_REQUIRED':'Setup wizard未完成','NOT_FOUND':'条目不存在',
           'NAME_EXISTS':'名称已存在','MEMORY_FULL':'条目数已达上限','IN_USE':'条目正在使用或不能删除','NO_DRAFT':'没有打开的方法参数菜单',
           'NO_METHOD':'没有打开的工作屏','SAMPLE_TOO_LIGHT':'样品少于0.1 g','START_WEIGHT_TOLERANCE':'起始重量超出容差（称量辅助为Active）',
           'RESULT_MEMORY_FULL':'结果存储已满（关闭了Auto-delete）','QP_INCOMPATIBLE':'方法设置与QuickPredict不兼容','QP_NO_KNOWLEDGE':'尚未建立预测知识',
           'QP_DISABLED':'QuickPredict未启用','NO_PREDICTION':'没有可用的预测','PAN_NOT_CLEAR':'盘架上有物体（请清空）','WEIGHT_TOO_SMALL':'调整砝码至少30 g',
           'NO_TEMPERATURE_KIT':'未插入温度套件','CHAMBER_OPEN':'样品室未关闭','TOO_HOT':'仪器未冷却到50 °C以下','WRONG_PASSWORD':'用户名或密码错误',
           'TEMPERATURE_SPAN':'温度调整两点至少相差60 °C','NO_TEST_MEASUREMENT':'没有该方法的测试测量','NO_WIZARD':'Method Wizard未在进行',
           'QP_CONDITIONING':'QuickPredict Wizard：仪器正在升温或冷却（等待计时结束）','SC_AMBIENT_REQUIRED':'请先输入室温与湿度',
           'SC_LOT_REQUIRED':'请输入SmartCal批号','SC_NO_SUBSTANCE':'盘上没有新的SmartCal颗粒','CHAMBER_CLOSED':'样品室关闭'}


def export_mt_hx204_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=SLOT_ZH.get(key),origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'MtHx204Session.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key in ('acquisition.start',); pre=[]
        conditions={
         'acquisition.start':('供电、盖关闭、有样品、已重新初始化样品',['power.enabled','lid.position_m','sample.remaining_kg','cycle.phase'],'powered and lid_m<=1e-6 and remaining>=0.0001 and phase==idle'),
         'sample.configure':('盖打开且无采集，样品0.1–201g且模板存在',['lid.position_m','cycle.phase'],'lid_m>=0.1 and phase!=running; validate species/mass before mutation'),
         'method.configure':('采集时锁定方法',['cycle.phase'],'phase!=running'),
         'thermal.set_temperature':('采集时锁定温度；摄氏度步进1度',['cycle.phase'],'phase!=running and integral_celsius'),
         'lid.set_position':('供电且采集中不得开盖',['power.enabled','cycle.phase'],'powered and (position_m==0 or phase!=running)')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        elif key in TERMINAL and vis=='agent':
            pre=[dict(description_zh='终端开机（非待机）、Setup wizard已完成、已登录且所在组有该访问权限；测量/测试/调整进行时编辑与新流程被拒',
                      state_ids=['power.enabled','users.state','work.state','procedure.state'],enforced_by='runtime',check='_gate(rt, right, login, idle)')]
        elif key.startswith('panel.') and kind!='query':
            pre=[dict(description_zh='面板按当前屏幕与手册规则处理手势；待机时只有ON/OFF键起作用；被调用命令的前提同样适用（被拒时显示仪器自己的消息）',
                      state_ids=['power.enabled','users.state'],enforced_by='runtime',check='PanelUI gesture dispatch (panel_ui.json)')]
        errs=['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']
        if key in TERMINAL: errs=errs+[e for e in ERRORS_ZH if e not in errs]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.mt_hx204.MtHx204Session.invoke',invocation=f'session.invoke(instance_id="hx204_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；取消保留部分失重' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已蒸发质量。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=ERRORS_ZH.get(e,e) if key in TERMINAL else e,side_effects='none') for e in errs],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['heater','temperature_sensor','sample_pan','balance_sensor'],
      state_ids=['thermal.temperature_k','sample.remaining_kg','sample.evaporated_kg','balance.indicated_kg'],
      meaning_zh='公开沸点驱动合成失重，逐物质质量守恒，无水选择性',implementation='lumped',
      equations=['T_next=T_target+(T-T_target)*exp(-dt/tau)','k_i=0.002*exp(clip((T-Tboil_i)/30,-15,8)); m_next=m_i*exp(-k_i*dt)','m_initial_i=m_remaining_i+m_evaporated_i; displayed_mass=round(m/1e-7)*1e-7',
                 'terminal programs (tau=45 s): Standard T_set; Rapid min(230,1.4*T_set) for 3 min then T_set; Gentle 50 degC ramp to T_set over ramp time; Steps per-step T',
                 'switch-off criterion: (m(t-dt)-m(t))<dg with dg=1 mg, dt=10/20/50/90/140 s (1-5) or free (mg or % of start weight per dt); inactive for max(30 s, dt)+SOC delay',
                 'balance reading=((pan+sample)*(1+span_err)+zero_err)*span_corr-tare; sensor_C=a*(T_C*(1+gain)+offset)+b; heater drives sensor_C to the target',
                 'result: %MC=(WW-W)/WW*100, %DC=W/WW*100, %AM=(WW-W)/W*100, %AD=WW/W*100, g/kg x10 of %, -%MC; free factor value*factor+offset',
                 'SmartCal granulate (assumption): d(released)/dt=(m*(MC_c(T_true)+0.03*(rH_storage-50))/100-released)/60 s, MC_c linear through the window '
                 'centres 3.8/5.8/8.1/10.8 % at 70/100/130/160 degC; result %MCN=%MC-0.03*(rH_entered-50)'],
      parameters={'standard_tau_s':dict(value=45.,unit='s',meaning_zh='标准升温时间常数，未标定',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},
      source_ids=['nist_templates','simulation_assumption'],limitations=['未求解潜热能量守恒、扩散/活度/分解、厂家自动终点和真实传感器噪声。'])
    _ann.annotate_export('mt_hx204',caps,slots,{'drying':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'drying':mechanism})

"""CHEM-004: measured nD references, synthetic thermal/mixture optics, explicit calibration.

Front panel (panel.touch / panel.switch / panel.capture / panel.frame) and device layer (settings, logon, methods, quick settings, sample
list, data memory, checks, adjustments, user functions): the 7 in touch screen of the M series instrument software from the Abbemat 350/550
manual (panel_ui.json beside the contract), run by the generic engine _panel_ui.py and the shared Anton Paar device layer _antonpaar_touch.py
on top of the commands below (no second copy of the measuring logic)."""
from __future__ import annotations
import copy
from asset_runtime import _annotations as _ann
import json
import math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
ROOT=Path(__file__).resolve().parents[2]
ASSET_TYPE_ID='instrument.antonpaar_abbematadvanced'; CATALOG_ID='CHEM-004'; PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/antonpaar_abbematadvanced_refractive_library/templates.json'
MODE={'type':'string','enum':['refractive_index','calibrated_concentration']}
ND=number('1',1.26,1.72); NULL_ND={'anyOf':[ND,{'type':'null'}]}
FRACTION=number('1',0,1); NULL_FRACTION={'anyOf':[FRACTION,{'type':'null'}]}
POINT=obj({'index':ND,'mass_fraction':FRACTION})
RESULT=obj({'index':NULL_ND,'temperature_k':number('K',277.15,378.15),'wavelength_m':number('m',589.3e-9,589.3e-9),
 'mass_fraction':NULL_FRACTION,'phase':PHASE,'quality':{'type':'string','enum':['estimated','suspect','unavailable']},
 'evidence':{'type':'string','enum':['derived','synthetic','unavailable']},'concentration_evidence':{'type':'string','enum':['user_supplied_calibration','unavailable']},'template_source':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','display_digits'],'供电设定；断电取消进行中操作','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'thermal.set_temperature':(obj({'temperature_k':number('K',277.15,378.15)}),obj({'temperature_k':number('K',277.15,378.15)}),['peltier','temperature_sensor'],'写温控目标，不隐式推进时间','device_command','agent',{'temperature_k':293.15},['thermal.target_k']),
 'lid.set_angle':(obj({'angle_rad':number('rad',0,math.pi/2)}),obj({'angle_rad':number('rad',0,math.pi/2)}),['lid','lid_handle'],'被动盖宿主位置设置，采集时锁定；不是机器人技能','device_command','agent',{'angle_rad':math.pi/2},['lid.angle_rad']),
 'sample.configure':(obj({'volume_fractions':{'type':'object','additionalProperties':FRACTION},'volume_m3':number('m^3',70e-9,2e-6),'matrix_id':TEXT,'contamination_fraction':number('1',0,.2)}),obj({'configured':BOOL}),['sample_well','sapphire_prism'],'受信任宿主初始化液体账本，体积在装样参考温度定义','configuration','evaluator',{'volume_fractions':{'ethanol':1.},'volume_m3':100e-9,'matrix_id':'ethanol','contamination_fraction':0.},['sample.remaining_m3','sample.input_m3','sample.filled','cycle.phase','optical.indicated_index']),
 'method.configure':(obj({'mode':MODE}),obj({'mode':MODE}),['display_digits'],'仅nD或专属标定浓度；不自动套Brix','configuration','agent',{'mode':'refractive_index'},['method.mode']),
 'calibration.configure':(obj({'matrix_id':TEXT,'temperature_k':number('K',277.15,378.15),'points':{'type':'array','items':POINT,'minItems':2,'maxItems':32},'source':TEXT}),obj({'configured':BOOL}),['display_digits','sapphire_prism'],'用户提供的特定体系标定表；要求nD与浓度严格单调，不宣称厂家校准','configuration','agent',{'matrix_id':'ethanol','temperature_k':293.15,'points':[{'index':1.35,'mass_fraction':0.},{'index':1.38,'mass_fraction':1.}],'source':'synthetic demonstration only'},['calibration.configured']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['sapphire_prism','led_source','detector','display_digits'],'闭盖、填样、恒温后进行10s光学读数代理','device_command','agent',{},['cycle.phase','cycle.elapsed_s']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['display_digits'],'取消测量或清洗，保留实际已发生的体积转移','device_command','agent',{},['cycle.phase']),
 'cleaning.start':(obj({'volume_m3':number('m^3',70e-9,1e-4)}),obj({'phase':PHASE,'operation_id':TEXT}),['sample_well','sapphire_prism'],'开盖进行30s工程清洗；清洗液和样品参考体积分别守恒','device_command','agent',{'volume_m3':1e-6},['cycle.phase','cycle.elapsed_s','cleaner.input_m3','cleaner.remaining_m3']),
 'refractometry.measure':(obj({}),RESULT,['sapphire_prism','detector','display_digits'],'纯读最近nD、温度及有适用标定时的浓度','query','agent',{},[]),
}
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'thermal.target_k':(number('K',277.15,378.15),'K','setpoint','agent',['peltier'],('thermal.set_temperature','temperature_k')),
 'thermal.temperature_k':(number('K',277.15,378.15),'K','process_truth','evaluator',['sapphire_prism'],None),
 'thermal.indicated_temperature_k':(number('K',277.15,378.15),'K','derived','agent',['temperature_sensor','display_digits'],None),
 'lid.angle_rad':(number('rad',0,math.pi/2),'rad','scene','agent',['lid'],('lid.set_angle','angle_rad')),
 'method.mode':(MODE,'1','setpoint','agent',['display_digits'],None),
 'calibration.configured':(BOOL,'1','derived','agent',['display_digits'],None),
 'cycle.phase':(PHASE,'1','derived','agent',['display_digits'],None),
 'cycle.elapsed_s':(number('s',0),'s','derived','agent',['display_digits'],None),
 'cycle.operation':({'type':'string','enum':['none','measurement','cleaning']},'1','derived','agent',['display_digits'],None),
 'sample.filled':(BOOL,'1','derived','agent',['sample_well'],None),
 'optical.indicated_index':(NULL_ND,'1','derived','agent',['detector','display_digits'],None),
 'concentration.indicated_mass_fraction':(NULL_FRACTION,'1','derived','agent',['display_digits'],None),
 'sample.remaining_m3':(number('m^3',0),'m^3','process_truth','evaluator',['sample_well'],None),
 'sample.input_m3':(number('m^3',0),'m^3','process_truth','evaluator',['sample_well'],None),
 'sample.waste_m3':(number('m^3',0),'m^3','process_truth','evaluator',['sample_well'],None),
 'cleaner.input_m3':(number('m^3',0),'m^3','process_truth','evaluator',['sample_well'],None),
 'cleaner.remaining_m3':(number('m^3',0),'m^3','process_truth','evaluator',['sample_well'],None),
 'cleaner.waste_m3':(number('m^3',0),'m^3','process_truth','evaluator',['sample_well'],None),
}
def validate(schema,value):
    """Explicit supported JSON subset; finite numeric values, strict bool/array/const handling."""
    if 'anyOf' in schema:
        for sub in schema['anyOf']:
            try: validate(sub,value); return
            except ActionRejected: pass
        raise ActionRejected('INVALID_ARGUMENT')
    typ=schema.get('type')
    if typ=='number':
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not schema.get('minimum',-math.inf)<=value<=schema.get('maximum',math.inf): raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='boolean':
        if type(value) is not bool: raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='null':
        if value is not None: raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='string':
        if not isinstance(value,str): raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='array':
        if not isinstance(value,list) or not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',math.inf): raise ActionRejected('INVALID_ARGUMENT')
        for v in value: validate(schema['items'],v)
    elif typ=='object':
        if not isinstance(value,dict) or not set(schema.get('required',[]))<=value.keys(): raise ActionRejected('INVALID_ARGUMENT')
        for k,v in value.items():
            if not isinstance(k,str): raise ActionRejected('INVALID_ARGUMENT')
            sub=schema.get('properties',{}).get(k,schema.get('additionalProperties',True))
            if sub is False: raise ActionRejected('INVALID_ARGUMENT')
            if isinstance(sub,dict): validate(sub,v)
    if 'enum' in schema and value not in schema['enum']: raise ActionRejected('INVALID_ARGUMENT')
    if 'const' in schema and value!=schema['const']: raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- device layer and front panel (Abbemat 350/550 manual D22IB001PL-G, 'abm p.N')
from asset_runtime import _antonpaar_touch as _ap
ASSET_VERSION='1.1.2'
ASSET_DIR=ROOT/'assets/expansion_batch03/t4_measure/antonpaar_abbematadvanced'
SERIAL='AB5201-0001'                                                     # assumption: serial number of the simulated instrument
WAVELENGTH_NM=589.3
ND_RANGE=(1.26,1.72)                                                     # Abbemat 5201 datasheet
ACCURACY={'Most precise':(0.005,15.,0.00001),'Precise':(0.01,10.,0.00002),'Fast Measuring':(0.03,5.,0.00006)}   # abm Table 11.3 p.75 (550): K, s, nD
T_UNITS=['°C','°F','K']
QUANTITIES={                                                              # abm Appendix B p.130-132 (the quantities this simulation can supply)
 'nD':dict(label='Refractive Index',group='RI Basic Scales',kind='nD',unit='nD',units=['nD'],digits=5),
 'concentration':dict(label='Concentration (user calibration)',group='RI Basic Scales',kind='fraction',unit='% w/w',units=['% w/w','g/g','g/kg'],digits=2),
 'ri_temperature':dict(label='RI Temperature',group='Temperature',kind='temperature',unit='°C',units=T_UNITS,digits=2),
 'ri_set_temperature':dict(label='RI Set Temperature',group='Temperature',kind='temperature',unit='°C',units=T_UNITS,digits=2),
 'ri_wavelength':dict(label='RI Wavelength',group='System Settings',kind='number',unit='nm',units=['nm'],digits=0),
 'ri_condition':dict(label='RI Condition',group='Refractive Index',kind='text'),
 'master_condition':dict(label='Master Condition',group='System Settings',kind='text'),
 'sample_name':dict(label='Sample Name',group='System Settings',kind='text'),
 'method':dict(label='Method',group='System Settings',kind='text'),
 'measurement_type':dict(label='Measurement Type',group='System Settings',kind='text'),
 'user':dict(label='User',group='System Settings',kind='text'),
 'date':dict(label='Date',group='System Settings',kind='text'),
 'time':dict(label='Time',group='System Settings',kind='text'),
 'unique_id':dict(label='Unique Sample ID',group='System Settings',kind='text'),
 'error_state':dict(label='Sample Error State',group='System Settings',kind='text'),
 'data_field_1':dict(label='Data Field 1',group='System Settings',kind='text'),
 'data_field_2':dict(label='Data Field 2',group='System Settings',kind='text'),
 'data_field_3':dict(label='Data Field 3',group='System Settings',kind='text'),
 'last_check_name':dict(label='Last RI Check Name',group='System Settings',kind='text'),
 'last_check_state':dict(label='Last RI Check State',group='System Settings',kind='text'),
 'last_check_date':dict(label='Last RI Check Date',group='System Settings',kind='text'),
 'last_adjustment_date':dict(label='RI Last Adjustment Date',group='System Settings',kind='text'),
 'last_adjustment_user':dict(label='RI Last Adjustment User',group='System Settings',kind='text'),
 'serial_number':dict(label='RI Serial Number',group='System Settings',kind='text'),
}
METHOD_FIELDS=[                                                           # Methods > Method Settings > [method] > Refractometer Module (abm p.75-76)
 _ap.t_enum('ri.scale','Scale',('Refractive Index','User calibration table'),'Refractive Index',('p.72-74','-'),'refractometer',
            assumption='only nD and the user calibration of calibration.configure are loaded; the factory concentration scales of Table 11.1 are not part of the simulated instrument'),
 _ap.t_bool('ri.temp_control','RI control temperature',True,('p.75','-'),'refractometer'),
 _ap.t_num('ri.temperature','Temperature',4.,105.,20.,('p.72, p.75-76','-'),'refractometer',unit='°C',visible=('ri.temp_control',True)),
 _ap.t_enum('ri.accuracy','Measurement Accuracy',tuple(ACCURACY),'Precise',('p.75','-'),'refractometer',assumption='factory accuracy not given; Precise assumed'),
 _ap.t_num('ri.hold_s','Hold Time',0,1800,1,('p.75-76, p.133','-'),'refractometer',unit='s',integer=True),
 _ap.t_num('ri.timeout_s','Timeout',0,3600,200,('p.76, p.133','-'),'refractometer',unit='s',integer=True,assumption='0 s = no timeout'),
]
FAVORITE_PAGES=['messages','monitor','quick_settings','method_list','measured_data','checks_execution','one_point','two_point','air_reference',
 'check_management','standards','statistics','check_data','adjustment_data','audit_trail','ri_module','measurement_mode','display_layout',
 'result_output','limits','quick_management','method_management','method_visibility','sample_naming','data_fields','list_warnings',
 'mandatory_fields','functions','calculator','date_time','regional','input_units','network','instrument_name','screen_saver','beeps',
 'touch_calibration','keyboard_barcode','smtp','brightness','rs232_lims','security_level','audit_settings','esign','logon_settings',
 'increased_security','advanced_security','user_management','group_admin','printer_management','auto_printout','report_management',
 'select_logos','name_address','memory_settings','browser_settings','system_info','live_raw_data']
CHECK_SCHEMA=_ap.obj(dict(name=_ap.NAME,kind={'type':'string','enum':['Water Check','Custom Check','Reference Standard Check']},
 method=_ap.nullable(_ap.STR),standard=_ap.nullable(_ap.STR),temperature={'type':'string','enum':['20 °C','Method temperature']},gxp=_ap.BOOL,
 reminder=_ap.BOOL,interval_days=_ap.num('days',1,365,integer=True),prohibit=_ap.BOOL,max_dev=_ap.num('nD',0,0.1),
 tolerances={'type':'array','items':_ap.obj(dict(q=_ap.nullable(_ap.STR),lower=_ap.nullable(_ap.num('1')),upper=_ap.nullable(_ap.num('1')))),'maxItems':10}),
 optional=('method','standard','temperature','gxp','reminder','interval_days','prohibit','max_dev','tolerances'))
STANDARD_SCHEMA=_ap.obj(dict(name=_ap.NAME,temperature_c=_ap.num('°C',4,105),nd=_ap.num('nD',ND_RANGE[0],ND_RANGE[1]),serial={'type':'string','maxLength':30},
 reference={'type':'string','maxLength':30},expiry={'type':'string','maxLength':10}),optional=('serial','reference','expiry'))
FACTORY_ADJUST={'offset':0.,'slope':1.,'baseline':0.,'factory':True}
ADJUST_STANDARD=_ap.obj(dict(name=_ap.NAME,temperature_c=_ap.num('°C',4,105),nd=_ap.nullable(_ap.num('nD',ND_RANGE[0],ND_RANGE[1]))))
def water_nd(t_c):
    """Water reference nD at the method temperature (abm p.65 'Water (method temp.)'); assumption: quadratic fit around 1.33299 at 20 °C."""
    d=t_c-20.; return round(1.33299-8.0e-5*d-1.6e-6*d*d,6)


def _abm_method_temperature(m): return m['settings']['ri.temperature']
def _abm_apply(h,m,q):
    """A method / quick settings change reaches the measuring cell (scale -> method.mode, temperature control and set temperature)."""
    s=m['settings']; mode='calibrated_concentration' if s['ri.scale']=='User calibration table' else 'refractive_index'
    target=round(min(max(q.get('ri.temperature',s['ri.temperature'])+273.15,277.15),378.15),6)
    if mode!=h.mode or (s['ri.temp_control'] and abs(target-h.target_k)>1e-9): h.result=None
    h.mode=mode; h.temp_control=s['ri.temp_control']
    if h.temp_control: h.target_k=target
def _abm_run_params(h,m,q,run):
    s=m['settings']; acc=ACCURACY[q.get('ri.accuracy',s['ri.accuracy'])]; temp=q.get('ri.temperature',s['ri.temperature'])
    control=s['ri.temp_control'] or run['type']=='TS' or run['kind'] in ('check','adjust')
    if run['kind']=='check':
        c=next(x for x in h.ap['checks'] if x['name']==run['check'])
        if c['kind']=='Water Check' and c.get('temperature','20 °C')=='20 °C': temp=20.               # abm p.63: water checks at 20 °C or method temperature
        if c['kind']=='Reference Standard Check': temp=next(x for x in h.ap['standards'] if x['name']==c['standard'])['temperature_c']
    timeout=q.get('ri.timeout_s',s['ri.timeout_s'])
    return dict(control=control,temp_c=temp,tol_c=acc[0],stability_s=acc[1],hold_s=float(q.get('ri.hold_s',s['ri.hold_s'])),timeout_s=float(timeout) or None)
def _abm_precheck(h,run):
    _ap.need(h.lid_angle<=1e-6,'LID_OPEN')                               # the reading needs the closed cover (as acquisition.start)
    filled=h._values()['sample.filled']
    if run['kind']=='adjust' and run['adjust']['type']=='air': _ap.need(not filled,'PRISM_NOT_CLEAN')    # abm p.68: clean and dry prism
    if filled:
        for t in run['temps'] if run['kind']=='sample' else [run['params']['temp_c']]:
            for sid in h.remaining: h._check_phase(sid,t+273.15)
    if run['kind']=='sample' and _ap.method(h.ap,run['method'])['settings']['ri.scale']=='User calibration table':
        c=h.calibration; _ap.need(c is not None and c['matrix_id']==h.matrix_id and abs(c['temperature_k']-(run['params']['temp_c']+273.15))<=.05,'NO_CALIBRATION')
def _abm_begin(h,run):
    h._operation_count+=1; h.operation_id=f'{h.instance_id}:{run["kind"]}:{h._operation_count}'
    h.phase='running'; h.operation='measurement'; h.elapsed_s=0.; h.result=None
    return dict(phase=h.phase,operation_id=h.operation_id)
def _abm_set_temperature(h,c): h.target_k=round(min(max(c+273.15,277.15),378.15),6); h.temp_control=True
def _abm_temperature_ok(h,tol): return abs(h.temperature_k-h.target_k)<=tol+1e-12
def _abm_begin_reading(h,seconds):
    h._reading_s=float(seconds); h.phase='running'; h.operation='measurement'; h.elapsed_s=0.; h.result=None
    return h._values()['sample.filled']
def _abm_resume(h): h.phase='running'; h.operation='measurement'; h.elapsed_s=0.
def _abm_values(h,index,condition,conc=None):
    ap=h.ap; dt=_ap.device_dt(h); q=ap['quick']; c=max((x for x in ap['check_data']),key=lambda r:r['id'],default=None)
    a=ap['adjustments'][-1] if ap['adjustments'] else None
    return {'nD':index,'concentration':conc,'ri_temperature':round(h.temperature_k-273.15,3),'ri_set_temperature':round(h.target_k-273.15,3) if h.temp_control else None,
     'ri_wavelength':WAVELENGTH_NM,'ri_condition':condition,'master_condition':condition,'sample_name':q['sample_name'],'method':ap['active'],
     'measurement_type':_ap.MEASUREMENT_TYPES[q['type']],'user':ap['user'] or '','date':_ap.date_text(h,dt),'time':_ap.clock_text(h),'unique_id':'',
     'error_state':'','data_field_1':q['field1'],'data_field_2':q['field2'],'data_field_3':q['field3'],'last_check_name':c['check'] if c else '',
     'last_check_state':c['result'] if c else '','last_check_date':c['time'][:10] if c else '','last_adjustment_date':a['time'][:10] if a else '',
     'last_adjustment_user':a['user'] if a else '','serial_number':SERIAL}
def _abm_reading(h,empty=False):
    """Values of a finished reading: the indicated nD of the physics result, or 'no measured data' (abm p.143) without a valid value."""
    if empty or not h.result or h.result['index'] is None:
        out=dict(values=_abm_values(h,None,'no measured data'),condition='no measured data')
        if not h._values()['sample.filled']: out['air_nd']=h._air_signal()   # clean, dry prism: the air reading of Set Air Reference (abm p.68)
        return out
    return dict(values=_abm_values(h,h.result['index'],'valid',h.result['mass_fraction']),condition='valid')
def _abm_direct_done(h,reading):
    q=h.ap['quick']; run=dict(kind='sample',method=h.ap['active'],type='S',sample_name=q['sample_name'],field1=q['field1'],field2=q['field2'],field3=q['field3'],list_index=None)
    _ap.finish_measurement(h,run,[reading])
def _abm_fill_prompt(h,run):
    if run['kind']=='adjust':
        st=run['adjust']['standards'][run['k']]
        return f'Clean the prism, apply the standard "{st["name"]}" (nD {st["nd"]:.5f} at {st["temperature_c"]:.2f} °C), close the cover and tap <OK>.'
    return f'Clean the prism, apply fill {run["k"]+1} of {run["n"]}, close the cover and tap <OK>.'
def _abm_evaluate_check(h,c,reading):
    v=reading['values']; nd=v['nD']
    if c['kind']=='Custom Check':
        rows=[dict(q=t['q'],value=v.get(t['q']),lower=t['lower'],upper=t['upper']) for t in c.get('tolerances',[])]
        ok=bool(rows) and all(r['value'] is not None and r['lower']<=r['value']<=r['upper'] for r in rows)
        return dict(result='passed' if ok else 'not passed',reference=None,measured=nd,lower=None,upper=None,rows=rows)
    ref=1.33299 if c['kind']=='Water Check' else next(x for x in h.ap['standards'] if x['name']==c['standard'])['nd']   # water nD at 20 °C (assumption: 1.33299)
    dev=None if nd is None else nd-ref
    ok=dev is not None and abs(dev)<=c.get('max_dev',0.0001)+1e-12
    return dict(result='passed' if ok else 'not passed',reference=ref,measured=nd,lower=ref-c.get('max_dev',0.0001),upper=ref+c.get('max_dev',0.0001),rows=[])
def _abm_adjust_done(h,run):
    """Adjustment proposal from the readings (abm p.65-68): old / new nD, temperature, deviations and the recommendation; applied by adjustment.decide."""
    ad=run['adjust']; a=h.adjust; rd=run['readings']
    if ad['type']=='air':                                                 # the new air baseline cancels the air reading of this run
        air=rd[-1].get('air_nd'); ok=air is not None
        h.ap['procedure']=dict(kind='adjust_result',type='air',ok=ok,recommend='Apply' if ok else 'Reject',rows=[],
                               new=dict(a,baseline=-air,factory=False) if ok else None,temperature=rd[-1]['values']['ri_temperature'],standards=[])
        return
    if any(r['values']['nD'] is None for r in rd):
        h.ap['procedure']=dict(kind='adjust_result',type=ad['type'],ok=False,recommend='Reject',rows=[],new=None,temperature=None,standards=ad['standards']); return
    raw=[(r['values']['nD']-a['offset']-a['baseline'])/a['slope'] for r in rd]; refs=[s['nd'] for s in ad['standards']]
    if ad['type']=='one_point': new=dict(a,offset=a['offset']+refs[0]-rd[0]['values']['nD'],factory=False)
    else:
        slope=(refs[1]-refs[0])/(raw[1]-raw[0]) if abs(raw[1]-raw[0])>1e-12 else None
        new=None if slope is None else dict(a,slope=slope,offset=refs[0]-slope*raw[0]-a['baseline'],factory=False)
    rows=[dict(standard=s['name'],old=r['values']['nD'],new=s['nd'],temperature=r['values']['ri_temperature'],dev_abs=r['values']['nD']-s['nd'],
               dev_rel=(r['values']['nD']-s['nd'])/s['nd']) for s,r in zip(ad['standards'],rd)]
    ok=new is not None; rec='Apply' if ok and all(abs(x['dev_abs'])<=0.002 for x in rows) else 'Reject'    # assumption: recommend Apply within 0.002 nD
    h.ap['procedure']=dict(kind='adjust_result',type=ad['type'],ok=ok,recommend=rec,rows=rows,new=new,temperature=rows[-1]['temperature'],standards=ad['standards'])
def _abm_live(h):
    """Live values of the monitor mode (abm p.85): the current indicated nD with a sample on the prism, else no value."""
    ap=h.ap; run=ap['run']
    cond='temp. equilibration' if run and run['stage'] in ('equilibrating','hold') else 'measuring' if run and run['stage'] in ('measuring','empty') \
        else 'waiting for sample' if run else 'measuring' if h.phase=='running' and h.operation=='measurement' else 'monitor mode'
    n=h._live_index(); conc=h._concentration(n) if n is not None else None
    return _abm_values(h,n,cond,conc)
def _abm_run_state(h):
    ap=h.ap; run=ap['run']
    if run is None: return dict(active=False,kind=None,stage=None,k=0,n=0,prompt=None,progress=0.,sample='',temperature_c=None,check=None)
    frac=(run['k']+(min(h.elapsed_s/max(h._reading_s,1e-9),1.) if run['stage']=='measuring' else 0.))/max(run['n'],1)
    return dict(active=True,kind=run['kind'],stage=run['stage'],k=run['k'],n=run['n'],prompt=run['prompt'],progress=round(frac,4),
                sample=run['sample_name'] or f'Sample {ap["sample_no"]}',temperature_c=run.get('temp_c'),check=run['check'],type=run['type'])
def _abm_readout(h):
    """What the main screen shows (abm p.24-27): header, the method's output fields (live in monitor mode, else the frozen last result),
    the progress bar and the quick access status."""
    ap=h.ap; m=_ap.method(ap); run=ap['run']
    rec=ap['frozen_rec'] if ap['frozen'] is not None else None
    vals=rec['values'] if rec else _abm_live(h) if (ap['monitor'] or run or h.phase=='running') else {}
    fields=[]
    for f in m['fields']:
        if not f['q']: fields.append(dict(q='',label='',text='',unit='')); continue
        q=QUANTITIES.get(f['q'],dict(label=f['q'][3:],kind='number'))
        unit=(f.get('unit') or q.get('unit') or '') if q.get('kind')!='text' else ''
        fields.append(dict(q=f['q'],label=q['label'],text=_ap.fmt_value(PROFILE,f,vals.get(f['q']),ap) if vals else '',unit=unit))
    rs=_abm_run_state(h); name=ap['quick']['sample_name'] or f'#{ap["sample_no"]}'
    if run: progress=dict(text=f'{rs["sample"]}: '+{'equilibrating':'Temperature equilibration','hold':'Temperature equilibration','measuring':'Measuring',
                          'empty':'Measuring','await_fill':'Waiting for the next fill'}[run['stage']],fraction=rs['progress'],color='red')
    elif rec: progress=dict(text=f'{rec["sample_name"]}: Finished',fraction=1.,color='green')
    else: progress=dict(text='',fraction=0.,color='neutral')
    return dict(header=dict(method=m['name'],sample=rec['sample_name'] if rec and not run else name,clock=_ap.clock_text(h),user=ap['user'] or '',
                group=_ap.public_users(ap)['group'] or ''),fields=fields,progress=progress,status=_ap.status(ap),monitor=ap['monitor'],
                frozen=rec is not None,screen_off=_ap.screen_off(h),start_enabled=not run and h.phase!='running',sample_list_mode=ap['settings']['samples.mode']=='Sample List')
def _abm_raw(h):
    """Service > Live Raw Data (abm p.106-107): live sensor values; frozen like the main screen after a measurement."""
    if not h.powered: return {}
    n=h._true_index() if h._values()['sample.filled'] else None; a=h.adjust
    raw=None if n is None else round(h._raw_nd(n),6)
    run=h.ap['run']
    return {'nd_raw':'---' if raw is None else f'{raw:.6f}','nd':'---' if raw is None else f'{h._index():.6f}','prism_t':f'{h.temperature_k-273.15:.3f} °C',
            'set_t':f'{h.target_k-273.15:.2f} °C' if h.temp_control else 'off','control':'on' if h.temp_control else 'off','wavelength':f'{WAVELENGTH_NM} nm',
            'state':'measuring' if h.phase=='running' else 'stopped' if h.phase=='cancelled' else 'single measurement' if h.phase=='succeeded' else 'monitor',
            'substate':run['stage'] if run else ('finished' if h.phase=='succeeded' else '-'),'condition':_abm_live(h)['ri_condition'],
            'calc_nd':'nD = slope * raw + offset + air baseline','calc_conc':'user calibration table (calibration.configure)' if h.calibration else 'no user calibration',
            'calc_corr':f'slope {a["slope"]:.6f} / offset {a["offset"]:+.6f} / baseline {a["baseline"]:+.6f}'}
def _abm_system_info(h):
    return [['Instrument','Abbemat 5201 (simulated)'],['Serial number',SERIAL],['Measuring range',f'{ND_RANGE[0]} nD to {ND_RANGE[1]} nD'],
            ['Temperature range','4 °C to 105 °C'],['Wavelength',f'{WAVELENGTH_NM} nm'],['Data memory',f'{len(h.ap["data"])} / {PROFILE["memory_capacity"]} results'],
            ['Software','M series instrument software model (manual version 2.10)'],['Adjustment','factory' if h.adjust['factory'] else 'user adjustment']]
def _abm_adjust_start(h,a):
    t=a['type']; st=copy.deepcopy(a.get('standards',[]))
    for x in st:
        _ap.check(ADJUST_STANDARD,x)
        if x['nd'] is None: _ap.need(x['name'].startswith('Water'),'INVALID_ARGUMENT'); x['nd']=water_nd(x['temperature_c'])
    if t=='factory':                                                      # abm p.68: reset to the factory adjustment
        h.adjust=dict(FACTORY_ADJUST); _abm_store_adjustment(h,'Reset to Factory Adjustment',[],True); return dict(phase=h.phase,operation_id='')
    _ap.need(len(st)==(2 if t=='two_point' else 1 if t=='one_point' else 0),'INVALID_ARGUMENT')
    if t=='two_point': _ap.need(st[1]['nd']-st[0]['nd']>=0.05*(ND_RANGE[1]-ND_RANGE[0])-1e-12,'NOT_ALLOWED')   # abm p.67: 5 % of the range, lower first
    return _ap.run_start(h,dict(kind='adjust',adjust=dict(type=t,standards=copy.deepcopy(st))))
def _abm_store_adjustment(h,kind,rows,applied):
    ap=h.ap; rec=dict(id=ap['next_adj'],time=_ap.device_iso(h),type=kind,user=ap['user'] or '-',rows=copy.deepcopy(rows),applied=applied,
                      correction=dict(h.adjust))
    ap['next_adj']+=1; ap['adjustments'].append(rec); del ap['adjustments'][:-50]                 # abm p.68: the last 50 adjustments
    _ap.audit(h,f'{kind}: {"applied" if applied else "rejected"}')
def _abm_adjust_decide(h,a):
    p=h.ap['procedure']; _ap.need(p is not None and p['kind']=='adjust_result','NO_PROCEDURE')
    _ap.need(not a['apply'] or p['ok'],'NOT_ALLOWED')
    names={'one_point':'One-point Adjustment','two_point':'Two-point Adjustment','air':'Set Air Reference'}
    if a['apply']: h.adjust=dict(p['new']); _abm_store_adjustment(h,names[p['type']],p['rows'],True)
    else: _ap.audit(h,f'{names[p["type"]]}: rejected')
    h.ap['procedure']=None
    return dict(applied=a['apply'])
PROFILE=dict(settings=_ap.MSERIES_SETTINGS,method_fields=METHOD_FIELDS,quantities=QUANTITIES,factory_methods=[dict(name='Refractive Index',protected=False)],
 layouts=[('1 large + 4 small',5),('1 large',1),('2 large',2),('1 large + 2 small',3),('6 small',6)],
 default_fields=[dict(q='nD',unit='nD',digits=5),dict(q='ri_set_temperature',unit='°C',digits=2),dict(q='ri_temperature',unit='°C',digits=2),
                 dict(q='ri_wavelength',unit='nm',digits=0),dict(q='master_condition',unit='',digits=0)],
 default_output=[dict(q='nD',unit='nD',digits=5),dict(q='ri_temperature',unit='°C',digits=2),dict(q='master_condition',unit='',digits=0)],
 browser_default=['sample_name','method','nD','ri_temperature','master_condition','date','time'],
 quick_default=['sample_name','type','ri.temperature','ri.hold_s','ri.timeout_s','ri.accuracy'],quick_fields=['ri.temperature','ri.hold_s','ri.timeout_s','ri.accuracy'],
 quick_params=['sample_name','type','ri.temperature','ri.hold_s','ri.timeout_s','ri.accuracy','field1','field2','field3'],quick_locked=['sample_name','type'],
 modes=('S','C','MM','MF','TS'),mf_max=5,t_min_c=4.,t_max_c=105.,max_methods=200,memory_capacity=10000,memory_reminder=9900,check_capacity=100,
 serial=SERIAL,max_users=50,max_functions=80,password_case_sensitive=False,main_quantity='nD',temperature_quantity='ri_temperature',
 fixed_favorites=[dict(page='messages',name='Message List'),dict(page='monitor',name='Monitor Mode')],favorite_pages=FAVORITE_PAGES,
 factory_checks=[dict(name='Water Check',kind='Water Check',method=None,standard='Water',temperature='20 °C',gxp=False,reminder=False,interval_days=1,
                      prohibit=False,max_dev=0.0001,tolerances=[],factory=True,last=None)],
 factory_standards=[dict(name='Water',temperature_c=20.0,nd=1.33299,serial='',reference='',expiry='')],
 touch_targets=[[80,96],[720,96],[720,376],[80,376]],check_schema=CHECK_SCHEMA,standard_schema=STANDARD_SCHEMA,check_default=dict(method=None,standard=None,temperature='20 °C',gxp=False,reminder=False,
 interval_days=1,prohibit=False,max_dev=0.0001,tolerances=[]),display_size=(800,480),display_component='screen',
 rights={'adjustment.start':'adjustments','adjustment.decide':'adjustments'},handlers={'adjustment.start':_abm_adjust_start,'adjustment.decide':_abm_adjust_decide},
 apply_method=_abm_apply,method_temperature=_abm_method_temperature,run_params=_abm_run_params,precheck=_abm_precheck,begin=_abm_begin,
 set_temperature=_abm_set_temperature,temperature_ok=_abm_temperature_ok,begin_reading=_abm_begin_reading,resume=_abm_resume,reading=_abm_reading,
 direct_done=_abm_direct_done,fill_prompt=_abm_fill_prompt,adjust_done=_abm_adjust_done,evaluate_check=_abm_evaluate_check,run_state=_abm_run_state,
 readout=_abm_readout,system_info=_abm_system_info,timeout_text='Measurement timeout: the prism temperature is not stable.')
PROFILE['commands']=set(_ap.DEVICE_CAPS)
ADJ_CAPS={
 'adjustment.start':(_ap.obj(dict(type={'type':'string','enum':['one_point','two_point','air','factory']},standards={'type':'array','items':ADJUST_STANDARD,'maxItems':2}),optional=('standards',)),
                     _ap.obj(dict(phase=TEXT,operation_id=TEXT)),['screen','sapphire_prism','detector'],
                     '折光仪调整（Checks / Adjustments > Refractometer Adjustments）：单点/两点调整（逐个装标准液测量）、设空气参考（棱镜清洁干燥）、恢复出厂调整；结果待adjustment.decide确认',
                     'device_command','agent',{'type':'one_point','standards':[{'name':'Toluene','temperature_c':20.0,'nd':1.4967}]},['run.state','adjustment.state','cycle.phase']),
 'adjustment.decide':(_ap.obj(dict(apply=BOOL)),_ap.obj(dict(applied=BOOL)),['screen'],'调整结果：<Apply>写入新的修正（调整数据）或<Reject>放弃','device_command','agent',
                      {'apply':True},['adjustment.state','adjustment.correction','data.adjustments']),
 'sensor.configure_error':(_ap.obj(dict(offset_nd=_ap.num('nD',-.01,.01),gain=_ap.num('1',-.01,.01),baseline_nd=_ap.num('nD',-.01,.01))),
                           _ap.obj(dict(offset_nd=_ap.num('nD'),gain=_ap.num('1'),baseline_nd=_ap.num('nD'))),['detector','sapphire_prism'],
                           '评测者：设定折射率读数误差（零点偏移、斜率、空气基线），默认0即理想；调整按模型修正（sim-to-real误差另行研究）','configuration','evaluator',
                           {'offset_nd':0.,'gain':0.,'baseline_nd':0.},['sensor.errors']),
}
_PANEL_SPEC_PATH=ASSET_DIR/'panel_ui.json'
DEVICE_TUPLES=_ap.device_cap_tuples(PROFILE,{'clock.set':['screen'],'measurement.start':['screen','sapphire_prism','detector','peltier'],
 'measurement.continue':['screen','sapphire_prism'],'check.start':['screen','sapphire_prism','detector']})
CAPS.update(DEVICE_TUPLES); CAPS.update(ADJ_CAPS)
for _k,_v in _ap.device_slots(PROFILE).items(): SLOTS[_k]=_v
SLOTS.update({'thermal.control':(BOOL,'1','setpoint','agent',['peltier'],None),'adjustment.state':(_ap.nullable(_ap.ANYOBJ),'1','derived','agent',['screen'],None),
 'adjustment.correction':(_ap.obj(dict(offset=_ap.num('nD'),slope=_ap.num('1'),baseline=_ap.num('nD'),factory=BOOL)),'1','derived','agent',['detector'],None),
 'sensor.errors':(_ap.obj(dict(offset_nd=_ap.num('nD'),gain=_ap.num('1'),baseline_nd=_ap.num('nD'))),'1','process_truth','evaluator',['detector'],None),
 'display.raw':(_ap.ANYOBJ,'1','derived','agent',['screen'],None)})
import os as _os
_engine=_ap.panel_engine()
PANEL_SPEC=_engine.load_spec(_PANEL_SPEC_PATH,capabilities=set(CAPS)|set(_ap.panel_caps(json.loads(_PANEL_SPEC_PATH.read_text())))) \
    if _PANEL_SPEC_PATH.is_file() and not _os.environ.get('G07_PANEL_BUILD') else None    # the spec builder imports this module before the spec exists
PANEL_CAPS=_ap.panel_caps(PANEL_SPEC) if PANEL_SPEC else {}
CAPS.update(PANEL_CAPS)

class AntonpaarAbbematadvancedRuntime:
    AP_PROFILE=PROFILE; AP_CAPS=CAPS; PANEL_SPEC=PANEL_SPEC
    def __init__(self,instance_id='abbemat5201_01'):
        self.instance_id=instance_id; self.time_s=0.; self.revision=0; self.powered=False
        self.target_k=293.15; self.temperature_k=293.15; self.lid_angle=0.; self.mode='refractive_index'; self.calibration=None
        self.phase='idle'; self.operation='none'; self.elapsed_s=0.; self.operation_id=None; self._operation_count=0
        self.remaining={}; self.initial_species={}; self.waste_species={}; self.matrix_id=None; self.contamination=0.
        self.cleaner_input_m3=0.; self.cleaner_remaining_m3=0.; self.cleaner_waste_m3=0.; self._clean_start_m3=0.
        self.result=None; self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances']
        self._principals={'__public_agent__':'agent'}; self._requests={}; self._owners={}
        self.temp_control=True; self._reading_s=10.; self.sensor={'offset_nd':0.,'gain':0.,'baseline_nd':0.}; self.adjust=dict(FACTORY_ADJUST)
        self.ap=_ap.new_device(PROFILE); self.panel_state=None; self.panel_capture_dir=None
        self._panel_session=None; self._procedure_op=None    # the session whose panel gesture runs (its caller rules apply); the run of the adjustment result
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','evaluator','debug'): raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals: raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals: raise ActionRejected('ACCESS_DENIED')
        return AntonpaarAbbematadvancedSession(self,principal_id)
    def describe(self): return self.session().describe(instance_id=self.instance_id)
    def _values(self):
        return {'power.enabled':self.powered,'thermal.target_k':self.target_k,'thermal.temperature_k':self.temperature_k,
          'thermal.indicated_temperature_k':round(self.temperature_k-273.15,3)+273.15,'lid.angle_rad':self.lid_angle,'method.mode':self.mode,
          'calibration.configured':self.calibration is not None,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'cycle.operation':self.operation,
          'sample.filled':sum(self.remaining.values())>=70e-9-1e-15,
          'optical.indicated_index':self.result['index'] if self.result else None,
          'concentration.indicated_mass_fraction':self.result['mass_fraction'] if self.result else None,
          'sample.remaining_m3':sum(self.remaining.values()),'sample.input_m3':sum(self.initial_species.values()),'sample.waste_m3':sum(self.waste_species.values()),
          'cleaner.input_m3':self.cleaner_input_m3,'cleaner.remaining_m3':self.cleaner_remaining_m3,'cleaner.waste_m3':self.cleaner_waste_m3,
          'thermal.control':self.temp_control,'adjustment.correction':dict(self.adjust),'sensor.errors':dict(self.sensor)}
    def read(self,visibility='agent'):
        vals=dict(self._values(),**_ap.device_values(self),**{'adjustment.state':copy.deepcopy(self.ap['procedure']),'display.raw':_abm_raw(self)})
        return copy.deepcopy({k:v for k,v in vals.items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
    # ---- front panel: host protocol of the engine (_panel_ui.py through _antonpaar_touch) and the panel.* commands
    def panel_read(self):
        """What the panel firmware sees: the agent slots plus whether the screen is live and the boot count (a reboot restarts the GUI)."""
        out=self.read('agent'); ro=out['display.readout']
        out.update({'host.powered':self.powered,'host.power_epoch':self.ap['power_epoch'],'host.screen_off':ro['screen_off'],'host.rights':_ap.rights_of(self.ap),
                    'host.logged_on':self.ap['user'] is not None,'host.running':self.phase=='running' or self.ap['run'] is not None})
        return out
    def debug_snapshot(self): return dict(state=self.read('evaluator'),remaining=copy.deepcopy(self.remaining),input=copy.deepcopy(self.initial_species),waste=copy.deepcopy(self.waste_species),matrix_id=self.matrix_id)
    def _measure(self):
        if self.result: return copy.deepcopy(self.result)
        return dict(index=None,temperature_k=self._values()['thermal.indicated_temperature_k'],wavelength_m=589.3e-9,mass_fraction=None,phase=self.phase,
            quality='unavailable',evidence='unavailable',concentration_evidence='unavailable',template_source=TEMPLATE_REL)
    def _index(self):
        """Indicated nD: the optical model plus the evaluator-set sensor errors (default 0), corrected by the adjustment (factory: identity)."""
        a=self.adjust
        return round(a['slope']*self._raw_nd(self._true_index())+a['offset']+a['baseline'],6)
    def _raw_nd(self,n):
        """The detector's raw nD of a sample with true index n: zero offset, slope error and the air baseline signal (evaluator errors, default 0)."""
        e=self.sensor; return n+e['offset_nd']+e['gain']*(n-1.333)+self._air_signal()
    def _air_signal(self):
        """Clean, dry prism (air): the detector's baseline signal in nD (the evaluator air baseline error; Set Air Reference measures it)."""
        return self.sensor['baseline_nd']
    def _live_index(self):
        if not self.powered or sum(self.remaining.values())<70e-9-1e-15: return None
        n=self._index(); return n if ND_RANGE[0]<=n<=ND_RANGE[1] else None
    def _true_index(self):
        total=sum(self.remaining.values()); lorentz=0.
        for sid,v in self.remaining.items():
            t=self.templates[sid]; n=t['value']-.0004*(self.temperature_k-t['temperature_k'])
            lorentz+=v/total*(n*n-1)/(n*n+2)
        return round(math.sqrt((1+2*lorentz)/(1-lorentz))+.001*self.contamination,6)
    def _concentration(self,n):
        c=self.calibration
        if self.mode!='calibrated_concentration' or not c or c['matrix_id']!=self.matrix_id or abs(c['temperature_k']-self.temperature_k)>.05: return None
        p=c['points']
        if not p[0]['index']<=n<=p[-1]['index']: return None
        for a,b in zip(p,p[1:]):
            if a['index']<=n<=b['index']: return a['mass_fraction']+(b['mass_fraction']-a['mass_fraction'])*(n-a['index'])/(b['index']-a['index'])
    def _check_phase(self,sid,temp):
        row=self.templates[sid]
        if not row['runtime_eligible']: raise ActionRejected('PRECONDITION_FAILED')
        if not (row.get('melting_point_k') or 0)<temp<(row.get('boiling_point_k') or 10000): raise ActionRejected('PRECONDITION_FAILED')
        if (row.get('melting_point_k') is None or row.get('boiling_point_k') is None) and abs(temp-row['temperature_k'])>.001: raise ActionRejected('PRECONDITION_FAILED')
    def _start(self,kind):
        self._operation_count+=1; self.operation_id=f'{self.instance_id}:{kind}:{self._operation_count}'
        self.phase='running'; self.operation=kind; self.elapsed_s=0.; self.result=None
        return dict(phase=self.phase,operation_id=self.operation_id)
    def invoke(self,capability,**a):
        if capability not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability in DEVICE_TUPLES or capability in ('adjustment.start','adjustment.decide'): return _ap.device_invoke(self,capability,a)
        if capability in PANEL_CAPS: return _ap.panel_invoke(self,capability,a)
        validate(CAPS[capability][0],a)
        if capability=='sensor.configure_error':
            _ap.check(CAPS[capability][0],a); self.sensor=dict(a); self.revision+=1; return dict(a)
        if capability not in ('power.set','acquisition.stop','refractometry.measure') and self.phase=='running': raise ActionRejected('BUSY')
        if capability=='power.set':
            was=self.powered; self.powered=a['enabled']
            if not self.powered and self.phase=='running': self.phase='cancelled'
            if self.powered!=was: self._power_cycle(self.powered)
            r=dict(a)
        elif capability=='thermal.set_temperature': self.target_k=a['temperature_k']; self.result=None; r=dict(a)
        elif capability=='lid.set_angle': self.lid_angle=a['angle_rad']; r=dict(a)
        elif capability=='method.configure': self.mode=a['mode']; self.result=None; r=dict(a)
        elif capability=='calibration.configure':
            p=a['points']; delta=[b['mass_fraction']-x['mass_fraction'] for x,b in zip(p,p[1:])]
            if not a['matrix_id'].strip() or not a['source'].strip() or any(b['index']<=x['index'] for x,b in zip(p,p[1:])) or not (all(d>0 for d in delta) or all(d<0 for d in delta)): raise ActionRejected('INVALID_ARGUMENT')
            self.calibration=copy.deepcopy(a); self.result=None; r={'configured':True}
        elif capability=='sample.configure':
            f=a['volume_fractions']
            if not f or not set(f)<=self.templates.keys() or not math.isclose(sum(f.values()),1.,abs_tol=1e-9) or not a['matrix_id'].strip() or any(x<=0 for x in f.values()): raise ActionRejected('INVALID_ARGUMENT')
            if not self.powered or self.lid_angle<1.4 or sum(self.remaining.values())>1e-15 or self.cleaner_remaining_m3>1e-15: raise ActionRejected('PRECONDITION_FAILED')
            for sid in f: self._check_phase(sid,self.temperature_k)
            self.remaining={sid:fraction*a['volume_m3'] for sid,fraction in f.items()}
            for sid,v in self.remaining.items(): self.initial_species[sid]=self.initial_species.get(sid,0.)+v; self.waste_species.setdefault(sid,0.)
            self.matrix_id=a['matrix_id']; self.contamination=a['contamination_fraction']; self.result=None
            self.phase='idle'; self.operation='none'; self.elapsed_s=0.; r={'configured':True}
            if self.ap['run'] is None: self.operation_id=None   # a run waiting for the next fill keeps its operation (and owner)
        elif capability=='acquisition.start':
            if not self.powered or self.lid_angle>1e-6 or not self._values()['sample.filled'] or abs(self.temperature_k-self.target_k)>.05: raise ActionRejected('PRECONDITION_FAILED')
            for sid in self.remaining: self._check_phase(sid,self.target_k)
            n=self._index()
            if not 1.26<=n<=1.72 or (self.mode=='calibrated_concentration' and self._concentration(n) is None): raise ActionRejected('PRECONDITION_FAILED')
            self._reading_s=10.; r=self._start('measurement')
        elif capability=='cleaning.start':
            if not self.powered or self.lid_angle<1.4: raise ActionRejected('PRECONDITION_FAILED')
            self.cleaner_input_m3+=a['volume_m3']; self.cleaner_remaining_m3+=a['volume_m3']; self._clean_start_m3=self.cleaner_remaining_m3
            r=self._start('cleaning')
        elif capability=='acquisition.stop':
            if self.phase=='running': self.phase='cancelled'
            _ap.run_cancel(self)                                          # also a Multi Fill / adjustment waiting for the next fill
            r={'phase':self.phase}
        else: return self._measure()
        self.revision+=1; return copy.deepcopy(r)
    def _power_cycle(self,on):
        """Mains switch: the instrument boots with the auto logon user, an empty sample list and the monitor mode (abm p.23, p.33, p.82)."""
        ap=self.ap; ap['run']=None; ap['procedure']=None
        if on: ap['power_epoch']+=1; ap['boot_s']=self.time_s; ap['last_input_s']=self.time_s; ap['user']=_ap.auto_logon_user(ap); ap['sample_list']=[]; ap['monitor']=True; ap['frozen']=None
    def _waiting(self):
        run=self.ap['run']; return run is not None and run['stage'] in ('equilibrating','hold','empty')   # waiting for a fill: other operations (cleaning) run
    def _wait_left(self):
        run=self.ap['run']
        if run is None or run['stage'] not in ('hold','empty'): return 1.
        return max(run['hold_left'] if run['stage']=='hold' else run['empty_left'],1e-6)
    def step(self,dt_s):
        if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0: raise ValueError('finite positive dt_s required')
        left=float(dt_s)
        while left>1e-10:
            active=self.phase=='running' and not self._waiting(); duration=self._reading_s if self.operation=='measurement' else 30.
            h=min(left,1.,duration-self.elapsed_s) if active else min(left,1.,self._wait_left()); done=False
            target=self.target_k if self.powered and self.temp_control else 293.15; self.temperature_k=target+(self.temperature_k-target)*math.exp(-h/20.)
            if active:
                self.elapsed_s+=h
                if self.operation=='cleaning':
                    for sid,v in self.remaining.items():
                        transfer=v*(1-math.exp(-h/5.)); self.remaining[sid]-=transfer; self.waste_species[sid]+=transfer
                    flush=min(self.cleaner_remaining_m3,self._clean_start_m3*h/30.); self.cleaner_remaining_m3-=flush; self.cleaner_waste_m3+=flush
                    self.contamination*=math.exp(-h/8.)
                if self.elapsed_s>=duration-1e-9:
                    self.phase='succeeded'
                    if self.operation=='cleaning':
                        for sid,v in self.remaining.items(): self.waste_species[sid]+=v
                        self.remaining={}; self.matrix_id=None; self.cleaner_waste_m3+=self.cleaner_remaining_m3; self.cleaner_remaining_m3=0.
                    else:
                        n=self._index(); conc=self._concentration(n); valid=1.26<=n<=1.72
                        exact=len(self.remaining)==1 and self.contamination==0 and all(abs(self.temperature_k-self.templates[s]['temperature_k'])<1e-6 for s in self.remaining)
                        self.result=dict(index=n if valid else None,temperature_k=self._values()['thermal.indicated_temperature_k'],wavelength_m=589.3e-9,mass_fraction=conc,
                            phase='succeeded',quality='suspect' if not valid or self.contamination>.002 or (self.mode=='calibrated_concentration' and conc is None) else 'estimated',
                            evidence='derived' if exact else 'synthetic',concentration_evidence='user_supplied_calibration' if conc is not None else 'unavailable',template_source=TEMPLATE_REL)
                        done=True
            self.time_s+=h; left-=h
            if done: _ap.run_reading_done(self,_abm_reading(self))         # device layer: data memory / next reading of the run
            else: _ap.run_tick(self,h)
        _ap.tick(self)
        self.revision+=1; return self.read('evaluator')

class AntonpaarAbbematadvancedSession:
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
        meta=export_antonpaar_abbematadvanced_contract_metadata()
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
        self._guard(capability_id,arguments); before=h.operation_id; h._panel_session=self if capability_id in PANEL_CAPS else None
        try: result=h.invoke(capability_id,**arguments)
        finally: h._panel_session=None
        op=result.get('operation_id')
        if op: h._owners[op]=self._principal
        elif capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before: h._owners[h.operation_id]=self._principal   # a run started by a panel gesture belongs to the caller
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,
          status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response)); return response

    def _guard(self,capability_id,arguments):
        """Caller rule (direct call and the command a panel gesture calls alike): no stop / next fill / power-off of another agent's run."""
        _ap.owner_guard(self._host,self._principal,self._role,capability_id,arguments)

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

def export_antonpaar_abbematadvanced_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarAbbematadvancedSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key in ('acquisition.start','cleaning.start'); pre=[]
        conditions={
         'acquisition.start':('供电、盖闭、足量液体、恒温且方法标定适用',['power.enabled','lid.angle_rad','sample.filled','thermal.target_k','cycle.phase'],'powered and closed and filled and abs(T-target)<=0.05 and no active operation; selected calibration matches matrix and temperature'),
         'sample.configure':('受信任宿主、盖打开且空池，模板与相态可用',['lid.angle_rad','sample.remaining_m3','cleaner.remaining_m3'],'powered and lid>=1.4 and empty and no cleaner; fractions sum to 1; verify phase before mutation'),
         'calibration.configure':('无操作在运行；明确体系/来源，至少两点且nD及浓度严格单调',['cycle.phase'],'not running; n strictly increasing and concentration strictly monotonic'),
         'cleaning.start':('供电、盖打开、没有其他运行中的过程',['power.enabled','lid.angle_rad','cycle.phase'],'powered and lid>=1.4 and not running'),
         'lid.set_angle':('进行中过程锁定盖',['cycle.phase'],'not running'),
         'thermal.set_temperature':('运行中锁定温度',['cycle.phase'],'not running'),
         'method.configure':('运行中锁定方法',['cycle.phase'],'not running')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_abbematadvanced.AntonpaarAbbematadvancedSession.invoke',invocation=f'session.invoke(instance_id="abbemat5201_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；取消保留部分清洗参考体积转移' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已排入废液的参考体积。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['sapphire_prism','peltier','led_source','interference_filter','detector','sample_well'],
      state_ids=['thermal.temperature_k','optical.indicated_index','sample.remaining_m3','sample.input_m3','sample.waste_m3'],
      meaning_zh='文献单点nD、Lorentz–Lorenz理想混合、温度/污染工程代理；参考体积账本守恒',implementation='lumped',
      equations=['T_next=T_target+(T-T_target)*exp(-dt/20)',
      'n_i(T)=n_ref_i-0.0004*(T-T_ref_i); L=sum(phi_i*(n_i^2-1)/(n_i^2+2)); n=sqrt((1+2L)/(1-L))+0.001*contamination',
      'concentration=piecewise_linear_calibration(n) only for matching matrix and temperature; never universal Brix',
      'input_reference_volume_i=remaining_reference_volume_i+waste_reference_volume_i'],
      parameters={'dn_dt':dict(value=-.0004,unit='1/K',meaning_zh='未取得实测系数的统一热光代理',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},
      source_ids=['refractive_templates','simulation_assumption'],limitations=['温度系数、混合、污染响应均未标定；未重建光线追迹/临界角成像。','无样品密度时不宣称质量守恒；本过程守恒各物质在装样参考温度定义的体积账本。','用户标定曲线仅做数学插值，实验有效性需用户证据；不外推。'])
    for key,slot in slots.items():
        if key.endswith('_m3'): slot['meaning_zh']=(slot['meaning_zh'] or '')+'；装样参考温度下体积账本，不是真实膨胀后的几何体积'
    _ann.annotate_export('antonpaar_abbematadvanced',caps,slots,{'refractometry':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'refractometry':mechanism})

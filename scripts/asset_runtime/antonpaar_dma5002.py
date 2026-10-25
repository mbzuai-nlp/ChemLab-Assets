"""CHEM-003 DMA 5002. Literature density plus explicitly synthetic off-reference response.

Front panel (panel.touch / panel.switch / panel.capture / panel.frame) and device layer (settings, logon, methods, quick settings, sample
list, data memory, checks, air / water and special adjustments, air pump, U-View, user functions): the touch screen of the M series instrument
software from the DMA 4100/4500/5000 M manual and the M series General Software Functions guide (panel_ui.json beside the contract), run by
the generic engine _panel_ui.py and the shared Anton Paar device layer _antonpaar_touch.py on top of the commands below."""
from __future__ import annotations
import bisect
import copy
from asset_runtime import _annotations as _ann
import json
import math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected, number, obj, validate, BOOL, TEXT, PHASE


def _rejected(code,detail):
    e=ActionRejected(code);e.detail=detail;return e   # 1.2.1: a refusal whose message names the cause
ROOT=Path(__file__).resolve().parents[2]
PROTOCOL_VERSION='1.0.0'
ASSET_TYPE_ID='instrument.antonpaar_dma5002'
CATALOG_ID='CHEM-003'
TEMPLATE_REL='assets/reactions/antonpaar_dma5002_density_library/templates.json'
NULL_RHO={'anyOf':[number('kg/m^3',0,3000),{'type':'null'}]}
RESULT=obj({'density_kg_m3':NULL_RHO,'temperature_k':number('K',273.15,373.15),'phase':PHASE,
 'filled':BOOL,'bubble_warning':BOOL,'quality':{'type':'string','enum':['unavailable','estimated','suspect']},
 'evidence':{'type':'string','enum':['derived','synthetic','unavailable']},'template_source':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','display_digits'],'设定供电；断电取消过程','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'thermal.set_temperature':(obj({'temperature_k':number('K',273.15,373.15)}),obj({'temperature_k':number('K',273.15,373.15)}),['peltier','temperature_sensor','display_digits'],'目标温度；实际温度仅step变化','device_command','agent',{'temperature_k':293.15},['thermal.target_k']),
 'method.configure':(obj({'mode':{'type':'string','enum':['density']}}),obj({'mode':{'type':'string','enum':['density']}}),['display_digits'],'仅实现原始密度法；浓度换算方法未实现','configuration','agent',{'mode':'density'},['method.mode']),
 'sample.configure':(obj({'substance_id':TEXT,'volume_m3':number('m^3',1e-6,5e-6),'bubble_fraction':number('1',0,.2),'contamination_fraction':number('1',0,.2)}),obj({'configured':BOOL}),['sample_inlet','measuring_tube'],'受信任宿主初始化液体，不是机器人注射','configuration','evaluator',{'substance_id':'ethanol','volume_m3':1e-6,'bubble_fraction':0.,'contamination_fraction':0.},['sample.cell_kg','sample.total_input_kg','fill.detected','bubble.detected','density.indicated_kg_m3']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['measuring_tube','excitation','temperature_sensor','display_digits'],'恒温且填充后启动典型30秒测量','device_command','agent',{},['cycle.phase','cycle.elapsed_s']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['display_digits'],'取消测量或清洗，保留部分物料转移','device_command','agent',{},['cycle.phase']),
 'cleaning.start':(obj({'volume_m3':number('m^3',1e-6,1e-4)}),obj({'phase':PHASE,'operation_id':TEXT}),['sample_inlet','sample_outlet','measuring_tube'],'简化60秒清洗；清洗液密度1000kg/m³为代理','device_command','agent',{'volume_m3':1e-5},['cycle.phase','cycle.elapsed_s','cleaner.input_kg','cleaner.remaining_kg']),
 'density.measure':(obj({}),RESULT,['measuring_tube','display_digits'],'只读最近测量；不推进时间','query','agent',{},[]),
 'calibration.check':(obj({'reference_density_kg_m3':number('kg/m^3',0,3000),'reference_temperature_k':number('K',273.15,373.15)}),obj({'deviation_kg_m3':number('kg/m^3'),'adjusted':{'type':'boolean','const':False}}),['measuring_tube','display_digits'],'对照用户标准值计算偏差；不冒充厂家校准调整','query','agent',{'reference_density_kg_m3':789.3,'reference_temperature_k':293.15},[]),
}
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'thermal.target_k':(number('K',273.15,373.15),'K','setpoint','agent',['peltier'],('thermal.set_temperature','temperature_k')),
 'thermal.temperature_k':(number('K',273.15,373.15),'K','process_truth','evaluator',['measuring_tube'],None),
 'thermal.indicated_temperature_k':(number('K',273.15,373.15),'K','derived','agent',['temperature_sensor','display_digits'],None),
 'method.mode':({'type':'string','const':'density'},'1','setpoint','agent',['display_digits'],None),
 'cycle.phase':(PHASE,'1','derived','agent',['display_digits'],None),
 'cycle.elapsed_s':(number('s',0),'s','derived','agent',['display_digits'],None),
 'cycle.operation':({'type':'string','enum':['none','measurement','cleaning']},'1','derived','agent',['display_digits'],None),
 'fill.detected':(BOOL,'1','derived','agent',['fill_sensor'],None),
 'bubble.detected':(BOOL,'1','derived','agent',['camera'],None),
 'density.indicated_kg_m3':(NULL_RHO,'kg/m^3','derived','agent',['measuring_tube','display_digits'],None),
 'sample.cell_kg':(number('kg',0),'kg','process_truth','evaluator',['measuring_tube'],None),
 'sample.waste_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_outlet'],None),
 'sample.total_input_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_inlet'],None),
 'cleaner.input_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_inlet'],None),
 'cleaner.remaining_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_inlet'],None),
 'cleaner.waste_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_outlet'],None),
}

# ---------------------------------------------------------------- device layer and front panel (DMA M manual C76IB003EN-U 'dma p.N' = PDF page;
# M series General Software Functions XPAIB001EN-V 'gsf p.N')
from asset_runtime import _antonpaar_touch as _ap
ASSET_VERSION='1.2.1'
ASSET_DIR=ROOT/'assets/expansion_batch03/t4_measure/antonpaar_dma5002'
SERIAL='DMA5002-0001'                                                     # assumption: serial number of the simulated instrument
AMBIENT_HPA=1013.25                                                      # assumption: ambient pressure seen by the built-in sensor
PRECISION={'Predetermination':(0.3,30.,0.000005),'Equilibrium fast':(0.02,30.,0.000003),'Equilibrium medium':(0.01,40.,0.000002),
           'Equilibrium slow':(0.005,50.,0.000001)}      # temperature window K (assumption), reading s (dma p.45 approx. 30-40 s), RM deviation g/cm3 (dma p.37)
CHECK_DEV=0.00005                                                        # factory tolerance of air / water checks (DMA 5000 M, dma p.24)
T_UNITS=['°C','°F','K']
SYS=dict(group='System',kind='text')
QUANTITIES={                                                              # dma Appendix H p.60-62 (the quantities this simulation can supply)
 'density':dict(label='Density',group='Density',kind='density',unit='g/cm³',units=['g/cm³','kg/m³'],digits=6),
 'sg':dict(label='Specific Gravity',group='Density',kind='number',unit='',units=[''],digits=6),
 'sg_t04':dict(label='Specific Gravity t/04',group='Density',kind='number',unit='',units=[''],digits=6),
 'density_temperature':dict(label='Density Temperature',group='Density',kind='temperature',unit='°C',units=T_UNITS,digits=3),
 'density_set_temp':dict(label='Density Set Temp.',group='Density',kind='temperature',unit='°C',units=T_UNITS,digits=3),
 'density_condition':dict(label='Density Condition',group='Density',kind='text'),
 'repeat_dev':dict(label='Repeat Dev. Density',group='Density',kind='density',unit='g/cm³',units=['g/cm³','kg/m³'],digits=6),
 'd':dict(label='d',group='Density (Expert)',kind='number',unit='',units=[''],digits=6),
 'uview':dict(label='U-View',group='Density',kind='text'),
 'master_condition':dict(label='Master Condition',**SYS),'sample_name':dict(label='Sample Name',**SYS),'method':dict(label='Method',**SYS),
 'measurement_type':dict(label='Measurement Mode',**SYS),'user':dict(label='User',**SYS),'date':dict(label='Date',**SYS),'time':dict(label='Time',**SYS),
 'unique_id':dict(label='Unique Sample Id',**SYS),'error_state':dict(label='Sample Error State',**SYS),'data_field_1':dict(label='DataField 1',**SYS),
 'data_field_2':dict(label='DataField 2',**SYS),'data_field_3':dict(label='DataField 3',**SYS),'last_check_name':dict(label='Last Check Name',**SYS),
 'last_check_result':dict(label='Last Check Result',**SYS),'last_check_date':dict(label='Last Check Date',**SYS),'serial_number':dict(label='Serial Number',**SYS),
}
METHOD_FIELDS=[                                                           # Methods > Method Settings > [method] > Density Module (dma p.35)
 _ap.t_num('den.temperature','Temperature',0.,100.,20.,('p.35, p.45','-'),'density',unit='°C'),
 _ap.t_num('den.timeout_s','Timeout',30,3600,600,('p.35, p.67','-'),'density',unit='s',integer=True),
 _ap.t_enum('den.filling_check','FillingCheck',('Always active','Not active','Active during measurement'),'Always active',('p.35, p.67','-'),'density'),
 _ap.t_num('den.delay_s','Measurement delay',0,600,0,('p.35, p.67','-'),'density',unit='s',integer=True),
 _ap.t_enum('den.precision','Measurement finished by',tuple(PRECISION),'Predetermination',('p.35, p.67','-'),'density',page2=True,
            assumption='temperature windows and reading times of the precision classes are not given; 0.3 / 0.02 / 0.01 / 0.005 K and 30 / 30 / 40 / 50 s'),
]
DMA_SETTINGS=[
 _ap.t_enum('density.air_table','Air Table',('Spieweck and Bettin','CIPM'),'Spieweck and Bettin',('p.27','-'),'density_module'),
 _ap.t_enum('density.water_table','Water Table',('Spieweck and Bettin','CIPM / IAPWS'),'Spieweck and Bettin',('p.27','-'),'density_module'),
 _ap.t_bool('density.auto_air_check','Auto air check before each measurement',False,('p.26','-'),'density_module'),
 _ap.t_bool('density.adjustment_analysis','Adjustment analysis',False,('p.32','-'),'density_module'),
 _ap.t_enum('camera.auto_save','Automatically save a picture',('of each measurement','only if there is a filling warning','never'),'never',('p.55','-'),'camera',
            assumption='factory choice not given; never'),
 _ap.t_num('camera.luminance','Auto Exposure Luminance',1,100,50,('p.55','-'),'advanced_camera',integer=True,assumption='range and default not given'),
 _ap.t_num('camera.position','Camera position',-10,10,0,('p.55','-'),'uview',integer=True,assumption='steps of the position buttons not given: -10..10'),
 _ap.t_num('camera.position_x','Camera position (horizontal)',-10,10,0,('p.55','-'),'uview',integer=True,
           assumption='Fig. E-1 shows up / down / left / right position buttons; steps not given: -10..10'),
 _ap.t_bool('airpump.stop_stable','Stop air pump automatically when the density value is stable',True,('-','p.14'),'air_pump',assumption='default not given'),
 _ap.t_bool('airpump.stop_after','Stop air pump automatically after',True,('-','p.14'),'air_pump'),
 _ap.t_num('airpump.stop_after_s','Seconds',1,3600,700,('-','p.14'),'air_pump',unit='s',integer=True,visible=('airpump.stop_after',True)),
 _ap.t_bool('timeserver.use','Use Time Server',False,('-','p.13'),'time_server',reboot=True),
 _ap.t_text('timeserver.servers','Time servers',0,120,'pool.ntp.org',('-','p.13'),'time_server',visible=('timeserver.use',True)),
 _ap.t_enum('timeserver.zone','Time Zone',('UTC','Europe/Vienna','Europe/London','America/New_York','Asia/Shanghai'),'Europe/Vienna',('-','p.13'),'time_server',
            reboot=True,assumption='time zone list shortened'),
 _ap.t_bool('security.non_storage','Non storage mode',False,('-','p.29-30'),'non_storage',reboot=True),
 _ap.t_enum('security.value_visibility','Value Visibility',('Always','During measurement','Only when measurement is finished'),'Always',('-','p.30'),
            'value_visibility'),
 _ap.t_bool('security.show_temperature','Show Temperature Value(s)',True,('-','p.30'),'value_visibility'),
]
SETTINGS=_ap.MSERIES_SETTINGS+DMA_SETTINGS
SECTION_RIGHTS={'density_module':'setup_measuring','camera':'setup_measuring','air_pump':'setup_measuring','advanced_camera':'setup_control_panel',
                'time_server':'setup_control_panel','non_storage':'setup_security','value_visibility':'setup_security',
                'uview':'measure'}                                         # assumption: every user who measures may move the camera
FAVORITE_PAGES=['messages','monitor','uview','quick_settings','method_list','measured_data','checks','air_water','density_settings','check_management',
 'statistics','check_data','density_adjustment_data','audit_trail','density_module_method','measurement_mode','repeated_mode','formula_parameters',
 'display_layout','result_output','limits','quick_management','method_management','method_visibility','sample_naming','data_fields','list_warnings',
 'mandatory_fields','camera_settings','air_pump_settings','extrapolation','functions','calculator','date_time','regional','time_server','input_units',
 'network','instrument_name','screen_saver','beeps','keyboard_barcode','smtp','brightness','advanced_camera','security_level','non_storage',
 'value_visibility','audit_settings','esign','logon_settings','increased_security','advanced_security','user_management','group_admin','printer_management',
 'auto_printout','report_management','select_logos','name_address','memory_settings','browser_settings','system_info','live_raw_data']
CHECK_SCHEMA=_ap.obj(dict(name=_ap.NAME,kind={'type':'string','enum':['Air Check','Water Check','Custom Check']},method=_ap.nullable(_ap.STR),
 gxp=_ap.BOOL,reminder=_ap.BOOL,interval_days=_ap.num('days',1,365,integer=True),prohibit=_ap.BOOL,lower_dev=_ap.num('g/cm³',0,0.1),upper_dev=_ap.num('g/cm³',0,0.1),
 tolerances={'type':'array','items':_ap.obj(dict(q=_ap.nullable(_ap.STR),lower=_ap.nullable(_ap.num('1')),upper=_ap.nullable(_ap.num('1')))),'maxItems':10}),
 optional=('method','gxp','reminder','interval_days','prohibit','lower_dev','upper_dev','tolerances'))
FACTORY_ADJUST={'offset':0.,'slope':1.,'table':{},'factory':True,'pressure_offset_hpa':0.}
SPECIAL=_ap.obj(dict(slot=_ap.num('1',1,5,integer=True),name=_ap.NAME,unit={'type':'string','maxLength':20},temperature_c=_ap.num('°C',0,100),
                     ref1=_ap.num('1',-1e6,1e6),ref2=_ap.num('1',-1e6,1e6)))
def template_density(t,tk):
    """Density of a library liquid at tk in kg/m3: the record's reference table (water: CIPM 0-40 °C / IAPWS-95 above, 1 K steps, the
    convention of dma Appendix J) interpolated linearly; otherwise the synthetic 0.001 1/K expansion about the single literature point."""
    tab=t.get('density_table')
    if tab:
        pts=tab['points']; i=min(max(bisect.bisect_right([x for x,_ in pts],tk)-1,0),len(pts)-2)
        (x0,y0),(x1,y1)=pts[i],pts[i+1]
        return y0+(y1-y0)*(tk-x0)/(x1-x0)
    return t['value']/(1+.001*(tk-t['temperature_k']))
def template_is_reference(t,tk):
    """True when the density at tk comes from reference data (the literature point itself or the record's reference table)."""
    tab=t.get('density_table')
    return abs(tk-t['temperature_k'])<1e-6 or bool(tab and tab['points'][0][0]-1e-9<=tk<=tab['points'][-1][0]+1e-9)
def water_density(t_c,table='Spieweck and Bettin'):
    """Density of water in kg/m3 (dma Appendix J p.70: Spieweck / Bettin; CIPM (Tanaka) 0-40 °C)."""
    if table.startswith('CIPM') and 0<=t_c<=40:
        return 999.974950*(1-(t_c-3.983035)**2*(t_c+301.797)/(522528.9*(t_c+69.34881)))
    a=(999.83952,16.952577,-7.9905127e-3,-4.6241757e-5,1.0584601e-7,-2.8103006e-10)
    return sum(c*t_c**i for i,c in enumerate(a))/(1+1.6887236e-2*t_c)
def air_density(t_c,p_hpa,rh_pct=50.):
    """Density of air in kg/m3 (dma Appendix J p.69: (0.34844 p - h (0.252 t - 2.0582)) / (273.15 + t), h = relative humidity, 0.5 for 50 %)."""
    return (0.34844*p_hpa-rh_pct/100.*(0.252*t_c-2.0582))/(273.15+t_c)
def _dma_settings_t(h): return h.ap['settings']
def _dma_apply(h,m,q):
    target=round(min(max(q.get('den.temperature',m['settings']['den.temperature'])+273.15,273.15),373.15),6)
    if abs(target-h.target_k)>1e-9: h.result=None
    h.target_k=target
def _dma_method_temperature(m): return m['settings']['den.temperature']
def _dma_run_params(h,m,q,run):
    s=m['settings']; prec=PRECISION[q.get('den.precision',s['den.precision'])]
    return dict(control=True,temp_c=q.get('den.temperature',s['den.temperature']),tol_c=prec[0],stability_s=prec[1]+float(q.get('den.delay_s',s['den.delay_s'])),
                hold_s=0.,timeout_s=float(q.get('den.timeout_s',s['den.timeout_s'])),timeout_from='equilibrium',rm_dev_default=prec[2],
                precision=q.get('den.precision',s['den.precision']),filling_check=q.get('den.filling_check',s['den.filling_check']))
def _dma_precheck(h,run):
    _ap.need(not h.broken,'BROKEN_U_TUBE')                                 # dma p.57: turn off, contact Anton Paar
    if run['kind']=='adjust':
        st=run['adjust']['standards'][run['k']]
        if st.get('air'): _ap.need(h.cell_kg<=1e-12 and h.cleaner_remaining_kg<=1e-12,'CELL_NOT_EMPTY')   # dma p.26: clean and dry for air
        return
    if run['kind']=='check' and next(c for c in h.ap['checks'] if c['name']==run['check'])['kind']=='Air Check':
        _ap.need(h.cell_kg<=1e-12 and h.cleaner_remaining_kg<=1e-12,'CELL_NOT_EMPTY')
        return
    if h.sid is not None:
        t=h.templates[h.sid]
        for tc in (run['temps'] if run['kind']=='sample' else [run['params']['temp_c']]):
            tk=tc+273.15
            _ap.need(not (t['phase']=='unresolved' and abs(tk-t['temperature_k'])>.001),'PRECONDITION_FAILED')
            _ap.need((t.get('melting_point_k') or 0)<tk<(t.get('boiling_point_k') or 10000),'PRECONDITION_FAILED')
def _dma_begin(h,run):
    h._operation_count+=1; h.operation_id=f'{h.instance_id}:{run["kind"]}:{h._operation_count}'
    h.phase='running'; h.operation='measurement'; h.elapsed_s=0.; h.result=None
    return dict(phase=h.phase,operation_id=h.operation_id)
def _dma_set_temperature(h,c): h.target_k=round(min(max(c+273.15,273.15),373.15),6)
def _dma_temperature_ok(h,tol): return abs(h.temperature_k-h.target_k)<=tol+1e-12
def _dma_begin_reading(h,seconds):
    h._reading_s=float(seconds); h.phase='running'; h.operation='measurement'; h.elapsed_s=0.; h.result=None
    return h._values()['fill.detected']
def _dma_resume(h): h.phase='running'; h.operation='measurement'; h.elapsed_s=0.
def _dma_uview(h):
    if h.cleaner_remaining_kg>1e-12 and h.cell_kg<=1e-12: return 'cleaning liquid'
    if h.cell_kg<=1e-12: return 'empty (air)'
    if h.volume_m3<1e-6: return 'partly filled'
    return 'filled, bubbles' if h.bubbles>.002 else 'filled'
UVIEW_PX=(160,60)                                                         # assumption: picture size of the simulated camera
def _dma_uview_image(h,zoom=False):
    """The U-View camera picture (dma p.19, p.40; Fig. E-1 p.55: the U-tube seen as a horizontal strip), drawn as grey levels from the filling
    state: a liquid-filled tube darker than an empty one, bubbles bright, a partly filled tube with its meniscus.  The camera position
    (camera.position_x / camera.position, 4 / 2 px per step) moves the view, so at large offsets the cell leaves the picture (dma p.55: adjust the
    position if the camera does not show the complete cell); Auto Exposure Luminance scales the brightness (50 = nominal; dma p.55: increase it to
    brighten the picture); <Zoom In> shows the centre at twice the scale."""
    s=h.ap['settings']; W,H=UVIEW_PX; state=_dma_uview(h); gain=s['camera.luminance']/50.
    dx,dy=4*s['camera.position_x'],2*s['camera.position']; f=2 if zoom else 1
    x0,x1,yc,half=14,146,30,9                                                 # the tube in the picture of the centred camera (pixels)
    inner={'empty (air)':215,'filled':105,'filled, bubbles':105,'partly filled':105,'cleaning liquid':150}[state]
    rows=[]
    for j in range(H):
        row=[]
        for i in range(W):
            x=(i-W/2)/f+W/2+dx; y=(j-H/2)/f+H/2+dy                            # scene point seen by this pixel
            v=70                                                              # the dark cell block behind the glass tube
            if x0<=x<=x1 and abs(y-yc)<=half:
                v=200 if abs(y-yc)>half-2 else inner                         # glass wall, then the tube content
                if state=='partly filled' and x>x0+0.55*(x1-x0) and abs(y-yc)<=half-2: v=215
                if state=='filled, bubbles' and abs(y-yc)<=half-2 and any((x-bx)**2+(y-yc-by)**2<=9 for bx,by in ((40,-2),(78,2),(118,-1))): v=240
            row.append(min(255,int(round(v*gain))))
        rows.append(row)
    return rows
def _dma_values(h,rho,condition,raw=None):
    """Output quantities of a reading; rho = indicated density in kg/m3 (None without a value); the special adjustments (dma p.28) give a
    value only at their own set temperature."""
    ap=h.ap; dt=_ap.device_dt(h); q=ap['quick']; c=max(ap['check_data'],key=lambda r:r['id'],default=None)
    tc=round(h.temperature_k-273.15,3); w=water_density(tc,ap['settings']['density.water_table'])
    sp={f'special:{k}':(round(x['a']*raw+x['b'],6) if raw is not None and abs(h.target_k-273.15-x['temperature_c'])<=0.005 else None) for k,x in ap['specials'].items()}
    return sp|{'density':None if rho is None else round(rho/1000.,7),'sg':None if rho is None else round(rho/w,6),'sg_t04':None if rho is None else round(rho/999.972,6),
     'd':None if rho is None else round((rho-w)/w,6),'density_temperature':tc,'density_set_temp':round(h.target_k-273.15,3),'density_condition':condition,
     'repeat_dev':None,'uview':_dma_uview(h),'master_condition':condition,'sample_name':q['sample_name'],'method':ap['active'],
     'measurement_type':_ap.MEASUREMENT_TYPES[q['type']],'user':ap['user'] or '','date':_ap.date_text(h,dt),'time':_ap.clock_text(h),'unique_id':'','error_state':'',
     'data_field_1':q['field1'],'data_field_2':q['field2'],'data_field_3':q['field3'],'last_check_name':c['check'] if c else '',
     'last_check_result':c['result'] if c else '','last_check_date':c['time'][:10] if c else '','serial_number':SERIAL}
def _dma_reading(h,empty=False):
    """A finished reading: the indicated density (predetermined at the set temperature with 'Predetermination'), the FillingCheck warning,
    'Error: No Oscillation' for a partly filled U-tube, air density for an empty one (dma p.42, p.56)."""
    run=h.ap['run']; params=run['params'] if run else {}
    if empty:
        if h.cell_kg>1e-12 or h.cleaner_remaining_kg>1e-12:
            return dict(values=_dma_values(h,None,'Error: No Oscillation'),condition='Error: No Oscillation',level='error',raw_kg_m3=None)
        raw=h._raw_air(); rho=h._indicate(raw)
        return dict(values=_dma_values(h,rho,'valid',raw),condition='valid',raw_kg_m3=raw)
    tk=h.target_k if params.get('precision')=='Predetermination' else h.temperature_k
    raw=h._raw_density(tk); rho=h._indicate(raw)
    cond,level='valid',None
    if params.get('filling_check','Always active')!='Not active' and h.bubbles>.002: cond,level='Filling Warning','warning'
    out=dict(values=_dma_values(h,rho,cond,raw),condition=cond,raw_kg_m3=raw)
    if level: out['level']=level
    auto=h.ap['settings']['camera.auto_save']
    out['camera_image']=auto=='of each measurement' or (auto=='only if there is a filling warning' and cond=='Filling Warning')
    return out
def _dma_direct_done(h,reading):
    q=h.ap['quick']; run=dict(kind='sample',method=h.ap['active'],type='S',sample_name=q['sample_name'],field1=q['field1'],field2=q['field2'],field3=q['field3'],list_index=None)
    _ap.finish_measurement(h,run,[reading])
def _dma_fill_prompt(h,run):
    if run['kind']=='adjust':
        st=run['adjust']['standards'][run['k']]
        if st.get('air'): return f'Rinse and dry the measuring cell (air pump), leave it empty and tap <OK> (air at {st["temperature_c"]:.0f} °C).'
        return f'Fill "{st["name"]}" bubble-free (check U-View) and tap <OK> ({st["temperature_c"]:.0f} °C).'
    return f'Exchange the sample: fill {run["k"]+1} of {run["n"]} bubble-free and tap <OK>.'
def _dma_reference(h,kind,t_c):
    s=h.ap['settings']
    return air_density(t_c,h._pressure_hpa()) if kind=='Air Check' else water_density(t_c,s['density.water_table'])
def _dma_evaluate_check(h,c,reading):
    v=reading['values']; rho=None if v['density'] is None else v['density']
    if c['kind']=='Custom Check':
        rows=[dict(q=t['q'],value=v.get(t['q']),lower=t['lower'],upper=t['upper']) for t in c.get('tolerances',[])]
        ok=bool(rows) and all(r['value'] is not None and r['lower']<=r['value']<=r['upper'] for r in rows)
        return dict(result='passed' if ok else 'not passed',reference=None,measured=rho,lower=None,upper=None,rows=rows,pressure_hpa=h._pressure_hpa())
    ref=_dma_reference(h,c['kind'],h.target_k-273.15)/1000.                # dma p.25: reference at the set temperature (air: and the measured pressure)
    lo,up=ref-c.get('lower_dev',CHECK_DEV),ref+c.get('upper_dev',CHECK_DEV)
    ok=rho is not None and lo-1e-12<=rho<=up+1e-12
    return dict(result='passed' if ok else 'not passed',reference=round(ref,7),measured=rho,lower=round(lo,7),upper=round(up,7),rows=[],pressure_hpa=h._pressure_hpa())
def _dma_pre_run(h,run):
    if run['kind']=='sample' and h.ap['settings']['density.auto_air_check']: return dict(kind='auto_air',state='prompt')
    return None
def _dma_pre_prompt(h,run):
    st=run['pre']['state'] if run.get('pre') else 'passed'
    return {'prompt':'Auto air check: clean and dry the measuring cell, then tap <OK>.',
            'failed':'Auto air check failed: clean and dry the cell, tap <OK> to repeat or <Cancel>.',
            'passed':'Auto air check passed: fill the sample and tap <OK>.'}[st]
def _dma_pre_done(h,run,reading):
    rho=reading['values']['density']; ref=air_density(h.target_k-273.15,h._pressure_hpa())/1000.
    return rho is not None and abs(rho-ref)<=CHECK_DEV+1e-12               # dma p.26: auto air check limits = air check limits
def _dma_pre_check_cell(h,run): return None
def _dma_adjust_done(h,run):
    """Adjustment proposal (dma p.26-30): old / new water density and deviations; special adjustments: new coefficients.  Applied by
    adjustment.decide (Apply / Reject)."""
    ad=run['adjust']; a=h.adjust; rd=run['readings']; st=ad['standards']
    if any(r['raw_kg_m3'] is None for r in rd):
        h.ap['procedure']=dict(kind='adjust_result',type=ad['type'],ok=False,recommend='Reject',rows=[],new=None,standards=st); return
    def pair(i,j):                                                        # air reading i, water / standard reading j -> slope / offset
        x1,x2=rd[i]['raw_kg_m3'],rd[j]['raw_kg_m3']; y1,y2=st[i]['ref'],st[j]['ref']
        if abs(x2-x1)<1e-9: return None
        k=(y2-y1)/(x2-x1); return dict(slope=k,offset=y1-k*x1)
    new=copy.deepcopy(a); rows=[]
    if ad['type']=='air_water':
        c=pair(0,1)
        if c: new.update(c,factory=False); new['table'].pop('20',None)
        w=st[1]['ref']; old=a['slope']*rd[1]['raw_kg_m3']+a['offset']; nw=None if not c else c['slope']*rd[1]['raw_kg_m3']+c['offset']
        rows=[dict(standard='Water',old=round(old/1000.,7),new=None if nw is None else round(nw/1000.,7),ref=round(w/1000.,7),
                   dev_abs=None if nw is None else round((old-nw)/1000.,7),dev_rel=None if nw is None else (old-nw)/nw)]
    elif ad['type']=='wide_range':                                        # air at 40 / 60 °C, water at 60 / 40 °C (dma p.28 Table 8-1)
        for t,(i,j) in ((40,(0,3)),(60,(1,2))):
            c=pair(i,j)
            if c: new['table'][str(t)]=c; rows.append(dict(standard=f'{t} °C',old=None,new=round(c['slope'],8),ref=round(c['offset'],5),dev_abs=None,dev_rel=None))
        new['factory']=False; c=bool(rows)
    else:
        sp=ad['special']; x1,x2=rd[0]['raw_kg_m3'],rd[1]['raw_kg_m3']
        c=abs(x2-x1)>=10.-1e-9                                            # dma p.30: the two densities differ by at least 0.01 g/cm3
        if c:
            k=(sp['ref2']-sp['ref1'])/(x2-x1); new=copy.deepcopy(a); new['special']=dict(sp,a=k,b=sp['ref1']-k*x1)
            rows=[dict(standard=sp['name'],old=None,new=round(k,8),ref=round(sp['ref1']-k*x1,6),dev_abs=None,dev_rel=None)]
    ok=bool(c); rec='Apply' if ok and all(r['dev_abs'] is None or abs(r['dev_abs'])<=0.0005 for r in rows) else 'Reject'   # assumption: 0.0005 g/cm3
    h.ap['procedure']=dict(kind='adjust_result',type=ad['type'],ok=ok,recommend=rec,rows=rows,new=new if ok else None,standards=st,
                           humidity_pct=ad.get('humidity_pct'),pressure_hpa=ad.get('pressure_hpa'),
                           temperature=None if ad['type']=='wide_range' else st[-1]['temperature_c'])
def _dma_adjust_start(h,a):
    """Checks / Adjustments (dma p.25-30): Air/Water at 20 °C, Air / Water Wide Range, Special Adjustments, Atmospheric Pressure Sensor Adjustment,
    Reset To Factory Adjustment."""
    t=a['type']; p=a.get('pressure_hpa',h._pressure_hpa())
    if t=='factory':
        h.adjust=dict(FACTORY_ADJUST,pressure_offset_hpa=h.adjust['pressure_offset_hpa']); _dma_store_adjustment(h,'Reset To Factory Adjustment',[],True)
        return dict(phase=h.phase,operation_id='')
    if t=='pressure':
        _ap.need('pressure_hpa' in a,'INVALID_ARGUMENT')
        off=h.adjust['pressure_offset_hpa']+a['pressure_hpa']-h._pressure_hpa(); h._procedure_op=None   # a result without a run
        h.ap['procedure']=dict(kind='adjust_result',type='pressure',ok=True,recommend='Apply',standards=[],barometer=a.get('barometer_id',''),
                               rows=[dict(standard='Air pressure',old=round(h._pressure_hpa(),2),new=a['pressure_hpa'],ref=None,dev_abs=None,dev_rel=None)],
                               new=dict(h.adjust,pressure_offset_hpa=off))
        return dict(phase=h.phase,operation_id='')
    rh=a.get('humidity_pct',50.)                                           # dma p.28: air humidity entered for the air adjustment (default 50 %)
    if t=='air_water':
        st=[dict(name='Air',air=True,temperature_c=20.,ref=air_density(20.,p,rh)),dict(name='Water',temperature_c=20.,ref=water_density(20.,h.ap['settings']['density.water_table']))]
    elif t=='wide_range':
        _ap.need(h.ap['settings']['density.air_table']=='Spieweck and Bettin','NOT_ALLOWED')   # dma p.28: only with the Spieweck / Bettin air table
        wt=h.ap['settings']['density.water_table']
        st=[dict(name='Air',air=True,temperature_c=40.,ref=air_density(40.,p,rh)),dict(name='Air',air=True,temperature_c=60.,ref=air_density(60.,p,rh)),
            dict(name='Water',temperature_c=60.,ref=water_density(60.,wt)),dict(name='Water',temperature_c=40.,ref=water_density(40.,wt))]
    else:
        _ap.need('special' in a,'INVALID_ARGUMENT'); sp=a['special']; _ap.check(SPECIAL,sp)
        _ap.need(not h.ap['settings']['security.non_storage'],'NOT_ALLOWED')   # dma p.28: not available in non storage mode
        st=[dict(name=f'{sp["name"]} standard 1',temperature_c=sp['temperature_c'],ref=sp['ref1']),dict(name=f'{sp["name"]} standard 2',temperature_c=sp['temperature_c'],ref=sp['ref2'])]
    return _ap.run_start(h,dict(kind='adjust',adjust=dict(type=t,standards=st,humidity_pct=a.get('humidity_pct',50.),pressure_hpa=p,special=a.get('special'))))
def _dma_store_adjustment(h,kind,rows,applied,extra=None):
    ap=h.ap; rec=dict(id=ap['next_adj'],time=_ap.device_iso(h),type=kind,user=ap['user'] or '-',rows=[r for r in copy.deepcopy(rows) if r.get('old') is not None]
                      if kind!='Special Adjustment' else [],applied=applied,b=round(h.adjust['offset'],6),**(extra or {}))
    ap['next_adj']+=1; ap['adjustments'].append(rec); del ap['adjustments'][:-50]                 # dma p.31: the last 50 adjustments
    _ap.audit(h,f'{kind}: {"applied" if applied else "rejected"}')
def _dma_adjust_decide(h,a):
    p=h.ap['procedure']; _ap.need(p is not None and p['kind']=='adjust_result','NO_PROCEDURE')
    _ap.need(not a['apply'] or p['ok'],'NOT_ALLOWED')
    names={'air_water':'Air / Water at 20 °C','wide_range':'Air / Water Wide Range','special':'Special Adjustment','pressure':'Atmospheric Pressure Sensor Adjustment'}
    if a['apply']:
        new=copy.deepcopy(p['new']); sp=new.pop('special',None)
        if sp: h.ap['specials'][str(sp['slot'])]=sp                       # a new output quantity in the group Special Adjustments
        else: h.adjust=new
        _dma_store_adjustment(h,names[p['type']],p['rows'],True,dict(barometer=p.get('barometer','')) if p['type']=='pressure' else None)
        if p['type']=='air_water' and h.ap['settings']['density.adjustment_analysis'] and abs(h.adjust['offset'])>0.1:   # assumption: 0.1 kg/m3
            _ap.add_message(h,'warning','Adjustment analysis: significant deviation from the reference adjustment.')
    else: _ap.audit(h,f'{names[p["type"]]}: rejected')
    h.ap['procedure']=None
    return dict(applied=a['apply'])
def _dma_air_pump(h,a):
    h.ap['air_pump']=dict(on=a['on'],since=h.time_s if a['on'] else None)
    return dict(on=a['on'])
def _dma_factory_reset(h,a):
    """Service > Reset to Factory Settings (gsf p.43): all data and individual settings erased; regional and network settings kept; reboot."""
    keep={k:v for k,v in h.ap['settings'].items() if k.startswith('region.') or k.startswith('network.')}
    epoch=h.ap['power_epoch']; h.ap=_ap.new_device(PROFILE); h.ap['settings'].update(keep); h.ap['power_epoch']=epoch
    h.adjust=dict(FACTORY_ADJUST); _ap._reboot(h)
    return dict(reset=True)
def _dma_extra_quantities(ap):
    return [dict(id=f'special:{k}',label=v['name'],group='Special Adjustments',units=[v['unit']],kind='number') for k,v in sorted(ap['specials'].items())]
def _dma_live(h):
    ap=h.ap; run=ap['run']
    cond='temp. equilibration' if run and run['stage'] in ('equilibrating','hold') else 'Aquisition' if run and run['stage'] in ('measuring','empty') \
        else 'waiting for sample' if run else 'Aquisition' if h.phase=='running' and h.operation=='measurement' else 'monitor mode'
    rho=None
    if h.powered and h._values()['fill.detected']: rho=h._indicate(h._raw_density(h.temperature_k))
    elif h.powered and h.cell_kg<=1e-12 and h.cleaner_remaining_kg<=1e-12: rho=h._indicate(h._raw_air())
    return _dma_values(h,rho,cond if h.cell_kg<=1e-12 or h.volume_m3>=1e-6 else 'Error: No Oscillation')
def _dma_run_state(h):
    ap=h.ap; run=ap['run']
    if run is None: return dict(active=False,kind=None,stage=None,k=0,n=0,prompt=None,progress=0.,sample='',temperature_c=None,check=None)
    frac=(run['k']+(min(h.elapsed_s/max(h._reading_s,1e-9),1.) if run['stage']=='measuring' else 0.))/max(run['n'],1)
    return dict(active=True,kind=run['kind'],stage=run['stage'],k=run['k'],n=run['n'],prompt=run['prompt'],progress=round(frac,4),
                sample=run['sample_name'] or f'Sample {ap["sample_no"]}',temperature_c=run.get('temp_c'),check=run['check'],type=run['type'],
                pre=(run.get('pre') or {}).get('state'))
def _dma_readout(h):
    """The main screen (dma p.18-19): header, output fields (live in monitor mode, frozen after a measurement; Value Visibility, gsf p.30),
    progress bar, quick access status (messages, air pump, frozen / unfrozen)."""
    ap=h.ap; m=_ap.method(ap); run=ap['run']; s=ap['settings']
    rec=ap['frozen_rec'] if ap['frozen'] is not None else None
    measuring=bool(run) or h.phase=='running'
    vals=rec['values'] if rec else _dma_live(h) if (ap['monitor'] or measuring) else {}
    vis=s['security.value_visibility']; show=vis=='Always' or (vis=='During measurement' and (measuring or rec)) or (vis=='Only when measurement is finished' and rec and not measuring)
    fields=[]
    for f in m['fields']:
        if not f['q']: fields.append(dict(q='',label='',text='',unit='')); continue
        q=QUANTITIES.get(f['q']) or next((x for x in _ap.quantities_list(PROFILE,ap) if x['id']==f['q']),dict(label=f['q'],kind='number'))
        unit=(f.get('unit') or q.get('unit') or '') if q.get('kind')!='text' else ''
        hide=not show and q.get('kind') not in ('text',) and not (q.get('kind')=='temperature' and s['security.show_temperature'])
        fields.append(dict(q=f['q'],label=q['label'],text='' if not vals or hide else _ap.fmt_value(PROFILE,f,vals.get(f['q']),ap),unit=unit))
    rs=_dma_run_state(h); name=ap['quick']['sample_name'] or f'#{ap["sample_no"]}'
    if run: progress=dict(text=f'{rs["sample"]}: '+{'equilibrating':'Temperature equilibration','hold':'Temperature equilibration','measuring':'Measuring',
                          'empty':'Measuring','await_fill':'Waiting for the next fill'}[run['stage']],fraction=rs['progress'],color='red')
    elif rec: progress=dict(text=f'{rec["sample_name"]}: Finished',fraction=1.,color='green')
    else: progress=dict(text='',fraction=0.,color='neutral')
    return dict(header=dict(method=m['name'],sample=rec['sample_name'] if rec and not run else name,clock=_ap.clock_text(h),user=ap['user'] or '',
                group=_ap.public_users(ap)['group'] or ''),fields=fields,progress=progress,status=_ap.status(ap),monitor=ap['monitor'],frozen=rec is not None,
                screen_off=_ap.screen_off(h),start_enabled=not run and h.phase!='running',sample_list_mode=s['samples.mode']=='Sample List',
                air_pump=ap['air_pump']['on'],uview=_dma_uview(h))
def _dma_system_info(h):
    return [['Instrument','DMA 5002 (simulated)'],['Serial number',SERIAL],['Measuring range','0 to 3 g/cm³'],['Temperature range','0 °C to 100 °C'],
            ['Data memory',f'{len(h.ap["data"])} / {PROFILE["memory_capacity"]} results'],['Software','M series instrument software model (manual version 2.93)'],
            ['Adjustment','factory' if h.adjust['factory'] else 'user adjustment'],['Non storage mode','on' if h.ap['settings']['security.non_storage'] else 'off']]
def _dma_raw(h):
    """Service > Live Raw Data (dma p.66, gsf p.43-44)."""
    if not h.powered: return {}
    lv=_dma_live(h); run=h.ap['run']
    return {'density_progress':str(int(round(100*min(h.elapsed_s/max(h._reading_s,1e-9),1.)))) if h.phase=='running' else '0',
            'fill_status':'0' if _dma_uview(h) in ('filled','empty (air)') else '1','status_value':'1' if abs(h.temperature_k-h.target_k)<=0.01 else '0',
            'density_raw':'---' if lv['density'] is None else f'{lv["density"]:.6f} g/cm³','cell_temperature':f'{h.temperature_k-273.15:.3f} °C',
            'calc_density':'Density = slope * raw + offset (adjustment)','calc_sg':'SG = Density / water density (water table)',
            'pump_state':'1' if h.ap['air_pump']['on'] else '0','air_pressure':f'{h._pressure_hpa():.2f} hPa','pressure_condition':'valid',
            'pressure_state':'measuring','pressure_substate':'-','heatsink_t':'25.0 °C','ntc_t':'25.0 °C','internal_t':'25.0 °C',
            'state':'measuring' if run else 'monitor'}
FACTORY_CHECKS=[dict(name='AirCheck',kind='Air Check',method=None,gxp=False,reminder=False,interval_days=1,prohibit=False,lower_dev=CHECK_DEV,upper_dev=CHECK_DEV,
                     tolerances=[],factory=True,last=None),
                dict(name='WaterCheck',kind='Water Check',method=None,gxp=False,reminder=False,interval_days=1,prohibit=False,lower_dev=CHECK_DEV,upper_dev=CHECK_DEV,
                     tolerances=[],factory=True,last=None)]
PROFILE=dict(settings=SETTINGS,section_rights=SECTION_RIGHTS,method_fields=METHOD_FIELDS,quantities=QUANTITIES,factory_methods=[dict(name='Density')],
 layouts=[('1 large + 4 small',5),('1 large',1),('2 large',2),('1 large + 2 small',3),('6 small',6)],
 default_fields=[dict(q='density',unit='g/cm³',digits=6),dict(q='sg',unit='',digits=6),dict(q='density_temperature',unit='°C',digits=3),
                 dict(q='density_condition',unit='',digits=0),dict(q='uview',unit='',digits=0)],
 default_output=[dict(q='density',unit='g/cm³',digits=6),dict(q='sg',unit='',digits=6),dict(q='density_temperature',unit='°C',digits=3),
                 dict(q='density_condition',unit='',digits=0)],
 browser_default=['sample_name','method','density','density_temperature','density_condition','date','time'],
 quick_default=['sample_name','type','den.temperature'],quick_fields=['den.temperature','den.precision','den.delay_s','den.timeout_s','den.filling_check'],
 quick_params=['sample_name','type','den.temperature','den.precision','den.delay_s','den.timeout_s','den.filling_check','field1','field2','field3'],
 quick_locked=['sample_name','type'],quick_hidden_for_operator=['den.temperature'],modes=('S','C','MM','RM','MF','TS','TTS'),mf_max=10,t_min_c=0.,t_max_c=100.,
 max_methods=400,memory_capacity=1000,memory_reminder=900,check_capacity=110,serial=SERIAL,max_users=1000,max_functions=150,password_case_sensitive=True,
 main_quantity='density',temperature_quantity='density_temperature',
 fixed_favorites=[dict(page='messages',name='Message List'),dict(page='uview',name='Camera Settings'),dict(page='air_pump',name='Air Pump'),
                  dict(page='monitor',name='Monitor Mode')],favorite_pages=FAVORITE_PAGES+['air_pump'],
 factory_checks=FACTORY_CHECKS,factory_standards=[],check_schema=CHECK_SCHEMA,
 check_default=dict(method=None,gxp=False,reminder=False,interval_days=1,prohibit=False,lower_dev=CHECK_DEV,upper_dev=CHECK_DEV,tolerances=[]),
 standard_schema=_ap.obj(dict(name=_ap.NAME)),display_size=(800,480),display_component='screen',touch_targets=[[80,96],[720,96],[720,376],[80,376]],
 extrapolation_default=[dict(value=15.,unit='°C'),dict(value=20.,unit='°C'),dict(value=29.5,unit='°C'),dict(value=60.,unit='°F')],
 method_extra_defaults=dict(api_group='A - Crude oil',api_input='Density',canadian_excise='None',rm_use_default=True),
 method_keys_extra=dict(api_group={'type':'string','enum':['A - Crude oil','B - Fuels','D - Lubes','Custom']},
                        api_input={'type':'string','enum':['Density']+[f'Special Adjustment {i}' for i in range(1,6)]},
                        canadian_excise={'type':'string','enum':['None']+[f'Special Adjustment {i}' for i in range(1,6)]},rm_use_default=BOOL),
 rights={'adjustment.start':'adjustments','adjustment.decide':'adjustments','air_pump.set':'measure','factory_settings.reset':'service'},
 handlers={'adjustment.start':_dma_adjust_start,'adjustment.decide':_dma_adjust_decide,'air_pump.set':_dma_air_pump,'factory_settings.reset':_dma_factory_reset},
 free_while_run=('air_pump.set',),apply_method=_dma_apply,method_temperature=_dma_method_temperature,run_params=_dma_run_params,precheck=_dma_precheck,
 begin=_dma_begin,set_temperature=_dma_set_temperature,temperature_ok=_dma_temperature_ok,begin_reading=_dma_begin_reading,resume=_dma_resume,reading=_dma_reading,
 direct_done=_dma_direct_done,fill_prompt=_dma_fill_prompt,adjust_done=_dma_adjust_done,evaluate_check=_dma_evaluate_check,run_state=_dma_run_state,
 readout=_dma_readout,system_info=_dma_system_info,timeout_text='Density Condition: Timeout Error.',pre_run=_dma_pre_run,pre_prompt=_dma_pre_prompt,
 pre_done=_dma_pre_done,pre_check_cell=_dma_pre_check_cell,extra_quantities=_dma_extra_quantities,device_extra=dict(specials={},air_pump=dict(on=False,since=None)))
PROFILE['first_fill']=lambda h,run: run['kind']=='adjust' and not run['adjust']['standards'][0].get('air')   # special: fill standard 1 first (dma p.30)
PROFILE['commands']=set(_ap.DEVICE_CAPS)-{'standards.manage'}
DMA_CAPS={
 'adjustment.start':(_ap.obj(dict(type={'type':'string','enum':['air_water','wide_range','special','pressure','factory']},humidity_pct=_ap.num('%',0,100),
                                  pressure_hpa=_ap.num('hPa',500,1100),barometer_id={'type':'string','maxLength':30},special=SPECIAL),
                             optional=('humidity_pct','pressure_hpa','barometer_id','special')),
                     _ap.obj(dict(phase=TEXT,operation_id=TEXT)),['screen','measuring_tube','excitation'],
                     '密度调整（Checks/Adjustments）：20 °C空气/水调整、宽温度范围空气/水调整、特殊调整（两个标准）、大气压传感器调整、恢复出厂调整；结果待adjustment.decide确认',
                     'device_command','agent',{'type':'air_water','humidity_pct':50.},['run.state','adjustment.state','cycle.phase']),
 'adjustment.decide':(_ap.obj(dict(apply=BOOL)),_ap.obj(dict(applied=BOOL)),['screen'],'调整结果：<Apply>写入新常数（调整数据）或<Reject>放弃','device_command','agent',
                      {'apply':True},['adjustment.state','adjustment.correction','data.adjustments']),
 'air_pump.set':(_ap.obj(dict(on=BOOL)),_ap.obj(dict(on=BOOL)),['screen'],'快捷区气泵开/关（干燥测量池）；按设置自动停止（gsf p.14）','device_command','agent',
                 {'on':True},['air.pump']),
 'factory_settings.reset':(_ap.obj({}),_ap.obj(dict(reset=BOOL)),['screen'],'Service > Reset to Factory Settings：清除数据与个人设置（保留区域与网络设置）并重启',
                           'device_command','agent',{},['device.settings','data.measured','methods.list']),
 'fault.inject':(_ap.obj(dict(code={'type':'string','enum':['broken_u_tube']})),_ap.obj(dict(code=TEXT,phase=TEXT)),['measuring_tube','screen'],
                 '评测者注入手册记载的错误“Broken U-tube.”（dma p.57表F-2：测量池损坏，关机并联系Anton Paar）：消息列表出现红色错误，运行中的测量/清洗停止，之后测量、核查、调整与清洗被拒（BROKEN_U_TUBE）；开机时再次报告','simulation','evaluator',
                 {'code':'broken_u_tube'},['device.messages','cycle.phase','run.state','fault.broken_u_tube']),
 'fault.clear':(_ap.obj({}),_ap.obj(dict(cleared=BOOL)),['measuring_tube'],'评测者：Anton Paar服务更换测量池后消除原因（消息仍须在消息列表确认）','simulation','evaluator',{},['fault.broken_u_tube']),
 'sensor.configure_error':(_ap.obj(dict(offset_kg_m3=_ap.num('kg/m^3',-5,5),gain=_ap.num('1',-.01,.01),pressure_offset_hpa=_ap.num('hPa',-50,50))),
                           _ap.obj(dict(offset_kg_m3=_ap.num('kg/m^3'),gain=_ap.num('1'),pressure_offset_hpa=_ap.num('hPa'))),['measuring_tube','excitation'],
                           '评测者：设定密度读数误差（偏移、斜率）与气压传感器偏移，默认0即理想；调整按模型修正（sim-to-real误差另行研究）','configuration','evaluator',
                           {'offset_kg_m3':0.,'gain':0.,'pressure_offset_hpa':0.},['sensor.errors']),
}
_PANEL_SPEC_PATH=ASSET_DIR/'panel_ui.json'
DEVICE_TUPLES=_ap.device_cap_tuples(PROFILE,{'measurement.start':['screen','measuring_tube','excitation','peltier'],'measurement.continue':['screen','measuring_tube'],
                                              'check.start':['screen','measuring_tube']})
CAPS.update(DEVICE_TUPLES); CAPS.update(DMA_CAPS)
for _k,_v in _ap.device_slots(PROFILE).items(): SLOTS[_k]=_v
SLOTS.update({'adjustment.state':(_ap.nullable(_ap.ANYOBJ),'1','derived','agent',['screen'],None),
 'adjustment.correction':(_ap.ANYOBJ,'1','derived','agent',['measuring_tube'],None),
 'sensor.errors':(_ap.obj(dict(offset_kg_m3=_ap.num('kg/m^3'),gain=_ap.num('1'),pressure_offset_hpa=_ap.num('hPa'))),'1','process_truth','evaluator',['measuring_tube'],None),
 'air.pump':(_ap.ANYOBJ,'1','setpoint','agent',['screen'],None),'air.pressure_hpa':(_ap.num('hPa'),'hPa','derived','agent',['electronics'],None),
 'display.raw':(_ap.ANYOBJ,'1','derived','agent',['screen'],None),
 'fault.broken_u_tube':(BOOL,'1','process_truth','evaluator',['measuring_tube'],None)})
import os as _os
_engine=_ap.panel_engine()
PANEL_SPEC=_engine.load_spec(_PANEL_SPEC_PATH,capabilities=set(CAPS)|set(_ap.panel_caps(json.loads(_PANEL_SPEC_PATH.read_text())))) \
    if _PANEL_SPEC_PATH.is_file() and not _os.environ.get('G07_PANEL_BUILD') else None
PANEL_CAPS=_ap.panel_caps(PANEL_SPEC) if PANEL_SPEC else {}
CAPS.update(PANEL_CAPS)


class AntonpaarDma5002Runtime:
    AP_PROFILE=PROFILE; AP_CAPS=CAPS; PANEL_SPEC=PANEL_SPEC
    PANEL_HOOKS={'uview_image':lambda ui,ctx,s:_dma_uview_image(ui.host.rt,bool(s.get('zoom'))) if ui.host.rt.powered else []}   # drawn only while shown
    def __init__(self,instance_id='dma5002_01'):
        self.instance_id=instance_id; self.time_s=0.; self.revision=0
        self.powered=False; self.target_k=293.15; self.temperature_k=293.15; self.mode='density'
        self.phase='idle'; self.operation='none'; self.elapsed_s=0.; self.operation_id=None; self._operation_count=0
        self.sid=None; self.volume_m3=0.; self.bubbles=0.; self.contamination=0.
        self.cell_kg=0.; self.waste_kg=0.; self.input_kg=0.
        self.cleaner_input_kg=0.; self.cleaner_remaining_kg=0.; self.cleaner_waste_kg=0.; self._clean_start_kg=0.
        self.result=None
        self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances']
        self._principals={'__public_agent__':'agent'}; self._requests={}; self._owners={}
        self._reading_s=30.; self.sensor={'offset_kg_m3':0.,'gain':0.,'pressure_offset_hpa':0.}; self.adjust=copy.deepcopy(FACTORY_ADJUST)
        self.ap=_ap.new_device(PROFILE); self.panel_state=None; self.panel_capture_dir=None
        self._panel_session=None; self._procedure_op=None    # the session whose panel gesture runs (its caller rules apply); the run of the adjustment result
        self.broken=False                                    # evaluator-injected 'Broken U-tube.' (dma p.57)
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','evaluator','debug'): raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals: raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals: raise ActionRejected('ACCESS_DENIED')
        return AntonpaarDma5002Session(self,principal_id)
    def describe(self): return self.session().describe(instance_id=self.instance_id)
    def _values(self):
        return {'power.enabled':self.powered,'thermal.target_k':self.target_k,'thermal.temperature_k':self.temperature_k,
          'thermal.indicated_temperature_k':round(self.temperature_k-273.15,3)+273.15,'method.mode':self.mode,
          'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'cycle.operation':self.operation,
          'fill.detected':self.cell_kg>1e-12 and self.volume_m3>=1e-6,'bubble.detected':self.bubbles>.002,
          'density.indicated_kg_m3':self.result['density_kg_m3'] if self.result else None,
          'sample.cell_kg':self.cell_kg,'sample.waste_kg':self.waste_kg,'sample.total_input_kg':self.input_kg,
          'cleaner.input_kg':self.cleaner_input_kg,'cleaner.remaining_kg':self.cleaner_remaining_kg,'cleaner.waste_kg':self.cleaner_waste_kg,
          'adjustment.correction':copy.deepcopy(self.adjust),'sensor.errors':dict(self.sensor),'air.pressure_hpa':round(self._pressure_hpa(),2)}
    def read(self,visibility='agent'):
        vals=dict(self._values(),**_ap.device_values(self),**{'adjustment.state':copy.deepcopy(self.ap['procedure']),'display.raw':_dma_raw(self),
                                                             'air.pump':copy.deepcopy(self.ap['air_pump']),'fault.broken_u_tube':self.broken})
        return copy.deepcopy({k:v for k,v in vals.items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
    # ---- front panel: host protocol of the engine (_panel_ui.py through _antonpaar_touch) and the panel.* commands
    def panel_read(self):
        """What the panel firmware sees: the agent slots plus whether the screen is live and the boot count (a reboot restarts the GUI)."""
        out=self.read('agent'); ro=out['display.readout']
        out.update({'host.powered':self.powered,'host.power_epoch':self.ap['power_epoch'],'host.screen_off':ro['screen_off'],'host.rights':_ap.rights_of(self.ap),
                    'host.logged_on':self.ap['user'] is not None,'host.running':self.phase=='running' or self.ap['run'] is not None})
        return out
    def _pressure_hpa(self): return AMBIENT_HPA+self.sensor['pressure_offset_hpa']+self.adjust['pressure_offset_hpa']
    def _raw_air(self):
        """Empty, dry U-tube: the density of air at the cell temperature and ambient pressure, plus the sensor errors (kg/m3)."""
        rho=air_density(self.temperature_k-273.15,AMBIENT_HPA); e=self.sensor
        return rho+e['offset_kg_m3']+e['gain']*(rho-998.)
    def _raw_density(self,tk):
        t=self.templates[self.sid]; rho=template_density(t,tk); rho=rho*(1-self.bubbles)+5*self.contamination
        e=self.sensor; return rho+e['offset_kg_m3']+e['gain']*(rho-998.)
    def _indicate(self,raw):
        """The adjustment (air / water: slope and offset; wide range: per temperature, linear in between) applied to a raw density, quantized."""
        a=self.adjust; tc=self.temperature_k-273.15; pts={20.:(a['slope'],a['offset'])}
        for k,v in a['table'].items(): pts[float(k)]=(v['slope'],v['offset'])
        xs=sorted(pts)
        if tc<=xs[0]: sl,of=pts[xs[0]]
        elif tc>=xs[-1]: sl,of=pts[xs[-1]]
        else:
            x0=max(x for x in xs if x<=tc); x1=min(x for x in xs if x>=tc); f=0. if x1==x0 else (tc-x0)/(x1-x0)
            sl=pts[x0][0]+f*(pts[x1][0]-pts[x0][0]); of=pts[x0][1]+f*(pts[x1][1]-pts[x0][1])
        return min(3000.,max(0.,round((sl*raw+of)/.005)*.005))
    def debug_snapshot(self): return dict(state=self.read('evaluator'),substance_id=self.sid,bubbles=self.bubbles,contamination=self.contamination)
    def _measure(self):
        if self.result: return copy.deepcopy(self.result)
        v=self._values()
        return dict(density_kg_m3=None,temperature_k=v['thermal.indicated_temperature_k'],phase=self.phase,
            filled=v['fill.detected'],bubble_warning=v['bubble.detected'],quality='unavailable',evidence='unavailable',template_source=TEMPLATE_REL)
    def _density(self):
        # beta is an uncalibrated engineering placeholder, not measured expansion; sensor errors (default 0) and the adjustment (factory: identity)
        return self._indicate(self._raw_density(self.temperature_k))
    def _start(self,kind):
        self._operation_count+=1; self.operation_id=f'{self.instance_id}:{kind}:{self._operation_count}'
        self.phase='running'; self.operation=kind; self.elapsed_s=0.; self.result=None
        return dict(phase=self.phase,operation_id=self.operation_id)
    def invoke(self,capability,**a):
        if capability not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability in DEVICE_TUPLES or capability in PROFILE['handlers']: return _ap.device_invoke(self,capability,a)
        if capability in PANEL_CAPS: return _ap.panel_invoke(self,capability,a)
        validate(CAPS[capability][0],a)
        if capability=='sensor.configure_error':
            _ap.check(CAPS[capability][0],a); self.sensor=dict(a); self.revision+=1; return dict(a)
        if capability=='fault.inject':                                    # dma p.57: the measuring cell is broken
            self.broken=True
            if self.phase=='running': self.phase='failed'
            _ap.run_cancel(self)
            if self.powered: _ap.add_message(self,'error','Broken U-tube.')
            self.revision+=1; return dict(code=a['code'],phase=self.phase)
        if capability=='fault.clear': c=self.broken; self.broken=False; self.revision+=1; return dict(cleared=c)
        if capability in ('acquisition.start','cleaning.start') and self.broken: raise ActionRejected('BROKEN_U_TUBE')
        if capability in ('thermal.set_temperature','method.configure','sample.configure','acquisition.start','cleaning.start') and self.phase=='running': raise ActionRejected('BUSY')
        if capability=='power.set':
            was=self.powered; self.powered=a['enabled']
            if not self.powered and self.phase=='running': self.phase='cancelled'
            if self.powered!=was: self._power_cycle(self.powered)
            r=dict(a)
        elif capability=='thermal.set_temperature':
            self.target_k=a['temperature_k']; self.result=None; r=dict(a)
        elif capability=='method.configure': self.mode=a['mode']; r=dict(a)
        elif capability=='sample.configure':
            sid=a['substance_id']
            why=('the instrument is switched off' if not self.powered else 'the cell still holds sample; drain or clean it first' if self.cell_kg>1e-12 else
                 'cleaning liquid is still in the cell; finish the cleaning first' if self.cleaner_remaining_kg>1e-12 else None)
            if why: raise _rejected('PRECONDITION_FAILED',why)   # 1.2.1: the message names the cause
            if sid not in self.templates: raise ActionRejected('INVALID_ARGUMENT')
            t=self.templates[sid]
            if not t.get('runtime_eligible',False): raise _rejected('PRECONDITION_FAILED','this substance has no runtime density reference')
            if t['phase']=='unresolved' and abs(self.temperature_k-t['temperature_k'])>.001: raise _rejected('PRECONDITION_FAILED','the density reference exists only at %.2f K; the cell is at %.2f K'%(t['temperature_k'],self.temperature_k))
            if not (t.get('melting_point_k') or 0)<self.temperature_k<(t.get('boiling_point_k') or 10000): raise _rejected('PRECONDITION_FAILED','the cell temperature %.2f K is outside the liquid range of the substance'%self.temperature_k)
            self.sid=sid; self.volume_m3=a['volume_m3']; self.bubbles=a['bubble_fraction']; self.contamination=a['contamination_fraction']
            self.cell_kg=template_density(t,self.temperature_k)*self.volume_m3*(1-self.bubbles)
            self.input_kg+=self.cell_kg; self.result=None; self.phase='idle'; self.operation='none'; self.elapsed_s=0.
            if self.ap['run'] is None: self.operation_id=None   # a run waiting for the next fill keeps its operation (and owner)
            r={'configured':True}
        elif capability=='acquisition.start':
            why=('the instrument is switched off' if not self.powered else 'the measuring cell is not filled' if not self._values()['fill.detected'] else
                 'the cell temperature %.3f K has not reached the set point %.3f K within 0.05 K'%(self.temperature_k,self.target_k) if abs(self.temperature_k-self.target_k)>.05 else None)
            if why: raise _rejected('PRECONDITION_FAILED',why)   # 1.2.1: the message names the cause
            t=self.templates[self.sid]
            if t['phase']=='unresolved' and abs(self.target_k-t['temperature_k'])>.001: raise _rejected('PRECONDITION_FAILED','the density reference exists only at %.2f K; set that temperature'%t['temperature_k'])
            if not (t.get('melting_point_k') or 0)<self.target_k<(t.get('boiling_point_k') or 10000): raise _rejected('PRECONDITION_FAILED','the set point %.2f K is outside the liquid range of the sample'%self.target_k)
            self._reading_s=30.; r=self._start('measurement')
        elif capability=='cleaning.start':
            if not self.powered: raise _rejected('PRECONDITION_FAILED','the instrument is switched off')
            mass=a['volume_m3']*1000.; self.cleaner_input_kg+=mass; self.cleaner_remaining_kg+=mass; self._clean_start_kg=self.cleaner_remaining_kg
            r=self._start('cleaning')
        elif capability=='acquisition.stop':
            if self.phase=='running': self.phase='cancelled'
            _ap.run_cancel(self)                                          # also a multiple filling / adjustment waiting for the next fill
            r={'phase':self.phase}
        elif capability=='calibration.check':
            if not self.result: raise _rejected('PRECONDITION_FAILED','there is no measurement result to check')
            if abs(a['reference_temperature_k']-self.result['temperature_k'])>.05: raise _rejected('PRECONDITION_FAILED','the reference temperature differs from the measured %.2f K by more than 0.05 K'%self.result['temperature_k'])
            return dict(deviation_kg_m3=self.result['density_kg_m3']-a['reference_density_kg_m3'],adjusted=False)
        else: return self._measure()
        self.revision+=1; return copy.deepcopy(r)
    def _power_cycle(self,on):
        """Power switch: the instrument boots with the auto logon user, an empty sample list and the monitor mode (dma p.23, gsf p.24)."""
        ap=self.ap; ap['run']=None; ap['procedure']=None; ap['air_pump']=dict(on=False,since=None)
        if on: ap['power_epoch']+=1; ap['boot_s']=self.time_s; ap['last_input_s']=self.time_s; ap['user']=_ap.auto_logon_user(ap); ap['sample_list']=[]; ap['monitor']=True; ap['frozen']=None; ap['frozen_rec']=None
        if on and self.broken: _ap.add_message(self,'error','Broken U-tube.')   # found again at power-up until the cell is replaced
    def _waiting(self):
        run=self.ap['run']; return run is not None and run['stage'] in ('equilibrating','hold','empty')   # waiting for a fill: other operations run
    def _wait_left(self):
        run=self.ap['run']
        if run is None or run['stage'] not in ('hold','empty'): return 1.
        return max(run['hold_left'] if run['stage']=='hold' else run['empty_left'],1e-6)
    def _air_pump_tick(self):
        """The air pump stops after the set time or when the density of the dry cell is stable (gsf p.14; assumption: stable 30 s after the cell is empty)."""
        p=self.ap['air_pump']; s=self.ap['settings']
        if not p['on']: return
        t=self.time_s-p['since']
        if (s['airpump.stop_after'] and t>=s['airpump.stop_after_s']-1e-9) or (s['airpump.stop_stable'] and self.cell_kg<=1e-12 and self.cleaner_remaining_kg<=1e-12 and t>=30.-1e-9):
            self.ap['air_pump']=dict(on=False,since=None)
    def step(self,dt_s):
        if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0: raise ValueError('finite positive dt_s required')
        left=float(dt_s)
        while left>1e-10:
            active=self.phase=='running' and not self._waiting(); duration=self._reading_s if self.operation=='measurement' else 60.
            h=min(left,1.,duration-self.elapsed_s) if active else min(left,1.,self._wait_left()); done=None
            target=self.target_k if self.powered else 293.15
            self.temperature_k=target+(self.temperature_k-target)*math.exp(-h/25.)
            if active:
                self.elapsed_s+=h
                if self.operation=='cleaning':
                    transfer=self.cell_kg*(1-math.exp(-h/10.)); self.cell_kg-=transfer; self.waste_kg+=transfer
                    self.volume_m3*=math.exp(-h/10.)
                    cleaner=min(self.cleaner_remaining_kg,self._clean_start_kg*h/60.); self.cleaner_remaining_kg-=cleaner; self.cleaner_waste_kg+=cleaner
                    self.contamination*=math.exp(-h/15.)
                if self.elapsed_s>=duration-1e-9:
                    self.phase='succeeded'
                    if self.operation=='cleaning':
                        self.waste_kg+=self.cell_kg; self.cell_kg=0.; self.volume_m3=0.; self.sid=None; self.bubbles=0.
                        self.cleaner_waste_kg+=self.cleaner_remaining_kg; self.cleaner_remaining_kg=0.
                    else:
                        t=self.templates[self.sid]; v=self._values()
                        self.result=dict(density_kg_m3=self._density(),temperature_k=v['thermal.indicated_temperature_k'],phase='succeeded',filled=True,
                            bubble_warning=v['bubble.detected'],quality='suspect' if self.bubbles>.002 or self.contamination>.002 else 'estimated',
                            evidence='derived' if template_is_reference(t,self.temperature_k) and self.bubbles==0 and self.contamination==0 else 'synthetic',template_source=TEMPLATE_REL)
                        done=_dma_reading(self)
                        if self.ap['run'] and done['values']['density'] is not None: self.result['density_kg_m3']=round(done['values']['density']*1000.,3)
            left-=h; self.time_s+=h
            if done is not None: _ap.run_reading_done(self,done)          # device layer: data memory / next reading of the run
            else: _ap.run_tick(self,h)
            self._air_pump_tick()
        _ap.tick(self)
        self.revision+=1; return self.read('evaluator')

class AntonpaarDma5002Session:
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
        meta=export_antonpaar_dma5002_contract_metadata()
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
                result={},observations={},error=dict(code=str(exc),message=getattr(exc,'detail',None) or str(exc),retryable=False,side_effects='none'))

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

def export_antonpaar_dma5002_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarDma5002Session.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key in ('acquisition.start','cleaning.start'); pre=[]
        conditions={
         'acquisition.start':('供电、液体填充、温差小于0.05K且无其他过程',['power.enabled','fill.detected','cycle.phase'],'powered and cell_kg>0 and abs(T-target)<=0.05 and not running'),
         'sample.configure':('宿主装样需空池、无剩余清洗液、已知相态允许液体',['power.enabled','sample.cell_kg','cleaner.remaining_kg'],'powered and cell_kg=0 and cleaner_remaining_kg=0; valid template and phase'),
         'cleaning.start':('供电且无运行中过程',['power.enabled','cycle.phase'],'powered and not running'),
         'thermal.set_temperature':('过程运行时温度锁定',['cycle.phase'],'not running'),
         'method.configure':('过程运行时方法锁定',['cycle.phase'],'not running'),
         'calibration.check':('有已完成测量且标准温度匹配',['density.indicated_kg_m3'],'result exists and abs(reference_temperature-result_temperature)<=0.05')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_dma5002.AntonpaarDma5002Session.invoke',invocation=f'session.invoke(instance_id="dma5002_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；取消保留部分清洗转移' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已排入废液的质量。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['measuring_tube','temperature_sensor','peltier','sample_inlet','sample_outlet'],
      state_ids=['thermal.temperature_k','sample.cell_kg','sample.waste_kg','sample.total_input_kg','density.indicated_kg_m3'],
      meaning_zh='公开单点液体密度与明确合成的温度/气泡/污染响应；清洗物料守恒',implementation='lumped',
      equations=['T_next=T_target+(T-T_target)*exp(-dt/25)',
      'rho=rho_ref/(1+0.001*(T-T_ref)) (water: linear interpolation in the 1 K CIPM / IAPWS-95 table of its library record); rho_display=quantize_0.005(rho*(1-bubble_fraction)+5*contamination_fraction)',
      'sample_input=sample_cell+sample_waste; cleaner_input=cleaner_remaining+cleaner_waste'],
      parameters={'temperature_tau_s':dict(value=25.,unit='s',meaning_zh='未标定集中参数热时间常数',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None),
      'thermal_expansion_per_k':dict(value=.001,unit='1/K',meaning_zh='所有物质共用的合成热膨胀占位；非实测系数（水除外：按库记录的CIPM / IAPWS-95参考表）',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},
      source_ids=['density_templates','simulation_assumption'],limitations=['未实现真实脉冲激励/黏度修正、厂家校准、压力、U-View图像或真实精度噪声。'])
    _ann.annotate_export('antonpaar_dma5002',caps,slots,{'density_measurement':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'density_measurement':mechanism})

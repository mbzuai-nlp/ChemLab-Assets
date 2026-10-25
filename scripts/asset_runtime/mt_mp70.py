"""MP70 four-capillary ramp/transmission surrogate; SI, no empirical video or purity assay.

Terminal layer (1.1.0): the MP70 control panel of the User Manual (5.7-inch VGA touch screen, Info / Home / Reset keys, power button with its
light): login, lock and shut-down, Methods and Start analysis, the analysis window (T(start), waiting time, ramp, manual Set, Stop), Results,
Manual operations and Setup, plus the front panel (panel.*: panel_ui.json beside the contract, run by the shared engine _panel_ui.py).  The
1.0.0 capabilities keep their meaning; the terminal commands are new; the furnace sensor error is host-settable and 0 by default.
1.2.0 (sync audit): an analysis started on the terminal (analysis.prepare) belongs to the calling session and another agent cannot stop or
change it; the screen saver starts after the user's delay; the Furnace inside view draws the reflected-light and transmitted-light images from
the capillary model; the Keyboards setting selects the on-screen layout; 'Warning' on expired adjustment data shows a warning."""
from __future__ import annotations
import copy, json, math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected, number, obj, BOOL, TEXT, PHASE


def _rejected(code,detail):
    e=ActionRejected(code);e.detail=detail;return e   # 1.2.2: a refusal whose message names the cause
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2]
ASSET_TYPE_ID='instrument.mt_mp70'; CATALOG_ID='CHEM-006'; PROTOCOL_VERSION='1.0.0'; ASSET_VERSION='1.2.2'
ASSET_DIR=ROOT/'assets/expansion_batch03/t4_measure/mt_mp70'
TEMPLATE_REL='assets/reactions/mt_mp70_thermal_library/templates.json'
TEMP=number('K',293.15,623.15); NULL_TEMP={'anyOf':[TEMP,{'type':'null'}]}; FRAC=number('1',0,1); NULL_FRAC={'anyOf':[FRAC,{'type':'null'}]}
CHANNEL={'type':'number','enum':[0,1,2,3],'x-unit':'1'}
METHOD=obj({'method_id':TEXT,'start_k':TEMP,'end_k':TEMP,'ramp_k_s':number('K/s',.1/60,20/60),'criterion':{'type':'string','enum':['transmission','visual_proxy']}})
POINT=obj({'time_s':number('s',0),'temperature_k':TEMP,'transmission':FRAC})
RESULT=obj({'channel':CHANNEL,'onset_k':NULL_TEMP,'clear_k':NULL_TEMP,'transmission':NULL_FRAC,'phase':PHASE,'quality':TEXT,'evidence':TEXT,'template_source':TEXT,'video_status':{'type':'string','const':'not_implemented'}})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','display_digits'],'供电；断电取消但保留样品状态','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'thermal.set_temperature':(obj({'temperature_k':TEMP}),obj({'temperature_k':TEMP}),['heater','temperature_sensor'],'写温度目标，实际升降温只由step推进','device_command','agent',{'temperature_k':293.15},['thermal.target_k']),
 'method.configure':(METHOD,obj({'stored_count':number('1',1,20)}),['display_digits'],'保存/替换最多20种温度和判定方法','configuration','agent',{'method_id':'demo','start_k':303.15,'end_k':363.15,'ramp_k_s':20/60,'criterion':'transmission'},['method.count']),
 'method.select':(obj({'method_id':TEXT}),obj({'method_id':TEXT}),['display_digits'],'选择已保存方法；不推进时间','configuration','agent',{'method_id':'demo'},['method.selected']),
 'sample.configure':(obj({'channel':CHANNEL,'substance_id':TEXT,'mass_kg':number('kg',1e-7,1e-4),'purity':number('1',.5,1),'particle_size_m':number('m',1e-6,1e-3),'decomposition_onset_k':NULL_TEMP,'decomposition_source':TEXT}),obj({'configured':BOOL}),['capillary_0','capillary_1','capillary_2','capillary_3'],'受信任宿主初始化独立毛细管固体样品；不是机器人装样','configuration','evaluator',{'channel':0,'substance_id':'benzoic_acid','mass_kg':2e-6,'purity':1.,'particle_size_m':50e-6,'decomposition_onset_k':None,'decomposition_source':''},['sample.input_kg','sample.retained_kg','sample.loaded_count']),
 'sample.unload':(obj({'channel':CHANNEL}),obj({'unloaded':BOOL}),['capillary_0','capillary_1','capillary_2','capillary_3'],'冷却后宿主卸载至外部接收账本','configuration','evaluator',{'channel':0},['sample.retained_kg','sample.removed_kg','sample.loaded_count']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['heater','led_source','camera_sensor','display_digits'],'预热、规定速率升温、透光阈值识别四路熔程代理','device_command','agent',{},['cycle.phase','cycle.elapsed_s','thermal.target_k']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['display_digits'],'取消后保留温度、熔化与分解进度和曲线','device_command','agent',{},['cycle.phase','thermal.target_k']),
 'melting.measure':(obj({'channel':CHANNEL}),RESULT,['camera_sensor','display_digits'],'纯读熔化起止代理；不返回样品身份或纯度','query','agent',{'channel':0},[]),
 'melting.curve':(obj({'channel':CHANNEL}),obj({'channel':CHANNEL,'points':{'type':'array','items':POINT},'evidence':{'type':'string','const':'synthetic'},'template_source':TEXT,'sample_interval_s':number('s',1,1),'truncated':BOOL}),['camera_sensor','display_digits'],'返回1s采样合成透光曲线，最多20000点，不是视频','query','agent',{'channel':0},[]),
}
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'thermal.target_k':(TEMP,'K','setpoint','agent',['heater'],('thermal.set_temperature','temperature_k')),
 'thermal.temperature_k':(TEMP,'K','process_truth','evaluator',['heater'],None),
 'thermal.indicated_temperature_k':(TEMP,'K','derived','agent',['temperature_sensor','display_digits'],None),
 'method.count':(number('1',0,20),'1','derived','agent',['display_digits'],None),
 'method.selected':(TEXT,'1','setpoint','agent',['display_digits'],('method.select','method_id')),
 'cycle.phase':(PHASE,'1','derived','agent',['display_digits'],None),
 'cycle.stage':({'type':'string','enum':['idle','preheat','ramp','hold']},'1','derived','agent',['heater'],None),
 'cycle.elapsed_s':(number('s',0),'s','derived','agent',['display_digits'],None),
 'sample.loaded_count':(number('1',0,4),'1','derived','agent',['capillary_0','capillary_1','capillary_2','capillary_3'],None),
 'sample.input_kg':(number('kg',0),'kg','process_truth','evaluator',['heater'],None),
 'sample.retained_kg':(number('kg',0),'kg','process_truth','evaluator',['heater'],None),
 'sample.removed_kg':(number('kg',0),'kg','process_truth','evaluator',['heater'],None),
 'sample.degraded_kg':(number('kg',0),'kg','process_truth','evaluator',['heater'],None),
}
for i in range(4):
    for field,schema,unit in [('onset_k',NULL_TEMP,'K'),('clear_k',NULL_TEMP,'K'),('transmission',NULL_FRAC,'1')]:
        SLOTS[f'channel_{i}.{field}']=(schema,unit,'derived','agent',['camera_sensor','display_digits'],None)
LEGACY_CAPS=tuple(CAPS); LEGACY_SLOTS=tuple(SLOTS)

# >>> CHEM-006 MP70 terminal
# The MP70 terminal of the User Manual (references/manuals/mt_mp70/operating_manual__mp70_mp80_mp90_user_manual.pdf, firmware 2.10; 'p.N' is the
# PDF page, printed page = N - 2): login / lock / shut down, Methods, Start analysis, analysis window (T(start), waiting time, ramp, Set, Stop),
# Results, Manual operations, Setup.  The User Manual gives the menu structure and the workflows, not every Setup parameter: those parameters are
# assumptions, listed as such in the spec.
from asset_runtime.mt_hx204 import (_b, _e, _n, _s, _f, _check, _defaults, _parse_time, _need, _settings_schema, _fmt_dec, CLOCK0,  # noqa: E402
                                    validate as _tvalidate)
import datetime, hashlib, os, tempfile, weakref  # noqa: E402,E401

MP_LANGUAGES=('English','Deutsch','Français','Italiano','Español','Português','Polski','Pусский','日本語','中文','한국어')
MP_KEYBOARDS=('English','Deutsch','Français','Italiano','Español')
MP_PREF_FIELDS=[
    _e('language','Language',MP_LANGUAGES,'English','p.12 (User settings > Language)',assumption='language list not given in the User Manual'),
    _n('brightness','Brightness',10,100,80,'p.12 (User settings > Screen)',unit='%',integer=True,assumption='screen parameters not given'),
    _n('screensaver_min','Screen saver after',1,120,15,'p.8, p.12',unit='min',integer=True,assumption='screen saver delay not given'),
    _b('beep','Beep',True,'p.12 (User settings > Beep)'),
    _e('keyboard','Keyboard',MP_KEYBOARDS,'English','p.12 (User settings > Keyboards)',assumption='layout list not given'),
]
MP_SETTINGS=[
    _s('id.instrument','Instrument ID',0,30,'MP70-1','p.13 (System > Identification)',section='identification',assumption='field set not given'),
    _e('region.date_format','Date format',('DD.MM.YYYY','MM/DD/YYYY','YYYY-MM-DD'),'DD.MM.YYYY','p.13 (System > Date / Time)',section='datetime',assumption='formats not given'),
    _e('region.time_format','Time format',('24:MM','12:MM'),'24:MM','p.13 (System > Date / Time)',section='datetime',assumption='formats not given'),
    _s('report.header','Header',0,40,'','p.13 (System > Header and footer)',section='header'),
    _s('report.footer','Footer',0,40,'','p.13 (System > Header and footer)',section='header'),
    _b('storage.sd','Save results on the SD card',True,'p.13 (System > Data storage), p.19 (SD card needed)',section='storage',assumption='single storage switch'),
    _b('policy.startup','Login without password at start (default user)',True,'p.15 (home screen or Login window at start)',section='policies',
       assumption='account policy fields not given'),
    _s('policy.startup_user','Default user',1,30,'Administrator','p.15',section='policies',vis=[('policy.startup','in',(True,))]),
    _b('seq.ask_save','Ask to save results after Stop',True,'p.37 (Do you want to save the results?)',section='sequence',assumption='analysis sequence settings not given'),
    _e('adj.expiry_action','Action on expiration of adjustment data',('None','Warning','Block'),'Warning','p.13',section='sequence',assumption='options not given'),
    _n('adj.validity_days','Adjustment valid for',1,3650,365,'p.13',unit='d',integer=True,section='sequence',assumption='interval not given'),
    _e('printer.type','Printer',('None','USB printer','Network printer'),'None','p.7 (USB / Ethernet), p.12 (Peripherals > Printer)',section='printer'),
    _e('net.config','Configuration',('DHCP','Manual'),'DHCP','p.12 (Hardware > Network)',section='network',assumption='network fields not given'),
    _s('net.ip','IP address',7,15,'192.168.1.10','p.12',section='network',vis=[('net.config','in',('Manual',))],pattern=r'^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$'),
    _s('netstore.path','Network storage path',0,64,'','p.12 (Hardware > Network storage)',section='netstore'),
    _s('netstore.user','User name',0,30,'','p.12',section='netstore'),
    _s('netstore.password','Password',0,30,'','p.12',section='netstore',secret=True),
]
MP_SETTING={f['key']:f for f in MP_SETTINGS}
MP_METHOD_FIELDS=[
    _s('title','Title',1,30,'New method','p.19 (Title)'),
    _e('type','Method type',('Melting point','Melting range'),'Melting point','p.10-11, p.19'),
    _n('start_c','Start temperature',20,350,50,'p.11, p.21',unit='°C',assumption='range from the runtime (20-350 °C)'),
    _n('waiting_s','Waiting time',0,3600,30,'p.11, p.20',unit='s',integer=True,assumption='range not given'),
    _n('rate_c_min','Heating rate',0.1,20.0,1.0,'p.11, p.21',unit='°C/min'),
    _n('end_c','End temperature',20,350,100,'p.11, p.21',unit='°C'),
    _e('determination','Determination',('Automatic','Manual'),'Automatic','p.20 (manual determination: Set per capillary)'),
    _e('criterion','Evaluation',('transmission','visual_proxy'),'transmission','p.10 (transmitted light)',assumption='the runtime evaluation of the melting'),
    _s('comment','Method comment',0,60,'','p.19'),
    _f('calibration','Calibration substance','text',None,'p.41 (calibration method of a reference substance)',nullable=True),
]
MANUAL_DEFAULT=dict(type='Melting point',start_c=50.0,waiting_s=30,rate_c_min=1.0,end_c=100.0)
MP_USER_FIELDS=[_s('user_name','Name',1,30,'','p.15'),_s('full_name','Full name',0,30,'','p.8 (User data)'),_b('active','Active',True,'p.15')]
MAX_SHORTCUTS=12; BOOT_S=10.0; FW='2.10'; SERIAL='B712345678'


def _mp_new_device():
    subs=[dict(name='Benzophenone',substance_id='benzophenone',nominal_c=48.5,tol_c=0.5),dict(name='Benzoic acid',substance_id='benzoic_acid',nominal_c=122.35,tol_c=0.5),
          dict(name='Caffeine',substance_id='caffeine',nominal_c=236.2,tol_c=0.5)]
    return dict(settings=_defaults(MP_SETTINGS),users=[dict(user_name='Administrator',full_name='',active=True,password=None,prefs=_defaults(MP_PREF_FIELDS),
                                                             shortcuts=[])],
                current_user='Administrator',locked=False,clock_offset_s=0.0,meta={},next_method=1,manual=dict(MANUAL_DEFAULT),last_start=None,
                analyses=[],next_analysis=1,substances=subs,calibrations=[],adjustment=dict(time=None,a=1.0,b=0.0,points=[]),sd_files=[],power_epoch=0)


TERMINAL={}
def term(name,inp,out,components,meaning,effects,example=None,kind='device_command',visibility='agent'):
    def deco(fn):
        TERMINAL[name]=fn; CAPS[name]=(inp,out,components,meaning,kind,visibility,example if example is not None else {},list(effects)); return fn
    return deco
OK_OUT=obj({'ok':BOOL}); NAME30=dict(type='string',minLength=1,maxLength=30); SCREEN=['screen','display_digits']
def _now(rt): return CLOCK0+datetime.timedelta(seconds=rt.time_s+rt.dev['clock_offset_s'])
def _user(dev,name=None):
    name=dev['current_user'] if name is None else name
    return next((u for u in dev['users'] if u['user_name']==name),None)
def _gate(rt,login=True,unlocked=True,idle=False):
    _need(rt.powered,'POWER_OFF')
    _need(rt.time_s>=rt.boot_until-1e-9,'STARTING_UP')
    if login: _need(rt.dev['current_user'] is not None,'LOGIN_REQUIRED')
    if unlocked: _need(not rt.dev['locked'],'LOCKED')
    if idle: _need(rt.phase!='running','BUSY')


@term('settings.configure',obj({'settings':_settings_schema(MP_SETTINGS)}),obj({'changed':dict(type='array',items=TEXT)}),SCREEN,
      'Setup设置（标识、日期时间格式、页眉页脚、数据存储、账户策略、分析行为、打印机、网络、网络存储）',['device.settings'],{'settings':{'report.header':'QC lab'}})
def _c_settings(rt,a):
    _gate(rt); new=dict(rt.dev['settings'])
    for k,v in a['settings'].items():
        f=MP_SETTING.get(k); _need(f is not None and _check(f,v),'INVALID_ARGUMENT'); new[k]=v
    if new['policy.startup']: _need(_user(rt.dev,new['policy.startup_user']) is not None,'NOT_FOUND')
    changed=sorted(k for k in new if new[k]!=rt.dev['settings'][k]); rt.dev['settings']=new
    return {'changed':changed}


@term('preferences.configure',obj({'preferences':_settings_schema(MP_PREF_FIELDS)}),obj({'changed':dict(type='array',items=TEXT)}),SCREEN,
      'User settings（语言、屏幕亮度、屏保时间、提示音、键盘）：当前用户',['users.state'],{'preferences':{'beep':False}})
def _c_prefs(rt,a):
    _gate(rt); u=_user(rt.dev); new=dict(u['prefs'])
    for k,v in a['preferences'].items():
        f=next(x for x in MP_PREF_FIELDS if x['key']==k); _need(_check(f,v),'INVALID_ARGUMENT'); new[k]=v
    changed=sorted(k for k in new if new[k]!=u['prefs'][k]); u['prefs']=new
    return {'changed':changed}


@term('clock.set',obj({'date':dict(type='string',pattern=r'^\d{4}-\d{2}-\d{2}$'),'time':dict(type='string',pattern=r'^\d{2}:\d{2}$')},optional=('date','time')),
      obj({'datetime':TEXT}),SCREEN,'System > Date / Time：设置日期时间',['device.clock'],{'time':'08:30'})
def _c_clock(rt,a):
    _gate(rt); now=_now(rt); d,t=now.date(),now.time()
    if 'date' in a:
        try: d=datetime.date.fromisoformat(a['date'])
        except ValueError: raise ActionRejected('INVALID_ARGUMENT') from None
    if 'time' in a:
        hm=_parse_time(a['time']); _need(hm is not None,'INVALID_ARGUMENT'); t=datetime.time(hm[0],hm[1])
    rt.dev['clock_offset_s']=(datetime.datetime.combine(d,t)-CLOCK0).total_seconds()-rt.time_s
    return {'datetime':_now(rt).isoformat(timespec='seconds')}


# ---- login, lock, users (p.15-16)
@term('session.login',obj({'user_name':NAME30,'password':dict(type='string',maxLength=30)},optional=('password',)),obj({'user_name':TEXT}),SCREEN,
      'Login窗口：选择用户名（有密码时输入密码）后Login；锁定时只有锁定用户能用密码解锁（p.15-16）',['users.state'],{'user_name':'Administrator'})
def _c_login(rt,a):
    _gate(rt,login=False,unlocked=False); dev=rt.dev
    u=_user(dev,a['user_name']); _need(u is not None and u['active'],'WRONG_PASSWORD')
    _need(u['password'] is None or a.get('password')==u['password'],'WRONG_PASSWORD')
    if dev['locked']: _need(u['user_name']==dev['current_user'],'LOCKED')
    dev['current_user']=u['user_name']; dev['locked']=False
    return {'user_name':u['user_name']}


@term('session.logout',obj({}),OK_OUT,SCREEN,'Home > Log out：注销并打开Login窗口',['users.state'])
def _c_logout(rt,a):
    _gate(rt); rt.dev['current_user']=None
    return {'ok':True}


@term('session.lock',obj({}),OK_OUT,SCREEN,'Home > Lock：锁定仪器（只有设了密码的用户可锁；运行任务继续，p.16）',['users.state'])
def _c_lock(rt,a):
    _gate(rt); _need(_user(rt.dev)['password'] is not None,'NO_PASSWORD'); rt.dev['locked']=True
    return {'ok':True}


@term('user.save',obj({'user_name':NAME30,'values':_settings_schema(MP_USER_FIELDS),'create':BOOL}),OK_OUT,SCREEN,'User management > User：新建或编辑用户',['users.state'],
      {'user_name':'Analyst','values':{'user_name':'Analyst','active':True},'create':True})
def _c_user_save(rt,a):
    _gate(rt); dev=rt.dev; v=a['values']
    for k,x in v.items():
        f=next(f for f in MP_USER_FIELDS if f['key']==k); _need(_check(f,x),'INVALID_ARGUMENT')
    _need(v.get('user_name',a['user_name'])==a['user_name'],'INVALID_ARGUMENT')
    if a['create']:
        _need(_user(dev,a['user_name']) is None,'NAME_EXISTS'); _need(len(dev['users'])<30,'MEMORY_FULL')
        u=dict(user_name=a['user_name'],full_name='',active=True,password=None,prefs=_defaults(MP_PREF_FIELDS),shortcuts=[]); dev['users'].append(u)
    else:
        u=_user(dev,a['user_name']); _need(u is not None,'NOT_FOUND')
        if u['user_name']=='Administrator': _need(v.get('active',True),'IN_USE')
    for k,x in v.items():
        if k!='user_name': u[k]=x
    return {'ok':True}


@term('user.delete',obj({'user_name':NAME30}),OK_OUT,SCREEN,'删除用户（不能删除Administrator或当前用户）',['users.state'],{'user_name':'Analyst'})
def _c_user_delete(rt,a):
    _gate(rt); dev=rt.dev; u=_user(dev,a['user_name']); _need(u is not None,'NOT_FOUND')
    _need(u['user_name'] not in ('Administrator',dev['current_user']),'IN_USE'); dev['users'].remove(u)
    return {'ok':True}


@term('user.set_password',obj({'user_name':NAME30,'password':{'type':['string','null'],'maxLength':30}}),OK_OUT,SCREEN,'设置/取消用户密码',['users.state'],
      {'user_name':'Administrator','password':'pw'})
def _c_user_pw(rt,a):
    _gate(rt); u=_user(rt.dev,a['user_name']); _need(u is not None,'NOT_FOUND'); _need(a['password'] is None or len(a['password'])>=1,'INVALID_ARGUMENT')
    u['password']=a['password']
    return {'ok':True}


@term('power.shutdown',obj({'forced':BOOL}),OK_OUT,SCREEN+['button_power'],'关机：Login窗口Shut down或电源键1-4 s（无任务时）；电源键超过5 s强制关机（运行中的分析终止，数据丢失，p.15-16）',
      ['power.enabled','cycle.phase','analysis.state'],{'forced':False})
def _c_shutdown(rt,a):
    _need(rt.powered,'POWER_OFF')
    if not a['forced']: _need(rt.phase!='running','BUSY')
    if rt.analysis is not None: rt.analysis['stage']='aborted'
    rt.invoke_legacy('power.set',enabled=False)
    return {'ok':True}


@term('display.screensaver',obj({'on':BOOL}),obj({'on':BOOL}),SCREEN+['button_power','power_led'],'电源键短按：开启/关闭屏保（屏保时电源灯渐亮渐暗，p.6）',['display.screensaver'],{'on':True})
def _c_saver(rt,a):
    _need(rt.powered,'POWER_OFF'); rt.screensaver=a['on']
    return {'on':rt.screensaver}


# ---- methods, shortcuts (p.10, p.19-21)
METHOD_ARGS=dict(type='object',properties={f['key']:(dict(type=['string','null'],maxLength=30) if f['key']=='calibration' else
                                                    dict(type='string',minLength=f['min_len'],maxLength=f['max_len']) if f['type']=='text' else
                                                    {'type':'string','enum':list(f['options'])} if f['type']=='enum' else
                                                    dict(number(f.get('unit') or '1',f['min'],f['max']),type='integer' if f['type']=='int' else 'number'))
                                               for f in MP_METHOD_FIELDS},required=[f['key'] for f in MP_METHOD_FIELDS],additionalProperties=False)
def _check_method(rt,v):
    for f in MP_METHOD_FIELDS:
        if f['key']=='calibration': _need(v['calibration'] is None or any(x['name']==v['calibration'] for x in rt.dev['substances']),'NOT_FOUND')
        else: _need(_check(f,v[f['key']]),'INVALID_ARGUMENT')
    _need(v['start_c']<v['end_c'],'INVALID_ARGUMENT')


@term('method.save',obj({'method_id':{'type':['string','null'],'maxLength':10},'values':METHOD_ARGS}),obj({'method_id':TEXT}),SCREEN,
      'Methods：新建（method_id=null，自动编号M0001…）或修改方法（类型、标题、起始/终止温度、等待时间、升温速率、判定、校准物质；最多20个）',
      ['method.count','methods.catalog'],{'method_id':None,'values':{'title':'Vanillin','type':'Melting point','start_c':75,'waiting_s':30,'rate_c_min':1.0,'end_c':90,
                                                                       'determination':'Automatic','criterion':'transmission','comment':'','calibration':None}})
def _c_method_save(rt,a):
    _gate(rt,idle=True); v=a['values']; _check_method(rt,v); dev=rt.dev
    mid=a['method_id']
    if mid is None:
        mid=f"M{dev['next_method']:04d}"
        _need(len(rt.methods)<20,'MEMORY_FULL')
    else: _need(mid in rt.methods,'NOT_FOUND')
    rt.invoke_legacy('method.configure',method_id=mid,start_k=v['start_c']+273.15,end_k=v['end_c']+273.15,ramp_k_s=v['rate_c_min']/60.0,criterion=v['criterion'])
    if a['method_id'] is None: dev['next_method']+=1
    dev['meta'][mid]=dict(v)
    return {'method_id':mid}


@term('method.delete',obj({'method_id':dict(type='string',maxLength=10)}),OK_OUT,SCREEN,'删除方法（及其快捷方式）',['method.count','methods.catalog','method.selected'],
      {'method_id':'M0001'})
def _c_method_delete(rt,a):
    _gate(rt,idle=True); _need(a['method_id'] in rt.methods,'NOT_FOUND')
    del rt.methods[a['method_id']]; rt.dev['meta'].pop(a['method_id'],None)
    if rt.selected==a['method_id']: rt.selected=''
    for u in rt.dev['users']: u['shortcuts']=[x for x in u['shortcuts'] if x['target']!=a['method_id']]
    return {'ok':True}


@term('shortcut.add',obj({'target':dict(type='string',maxLength=10),'direct':BOOL}),OK_OUT,SCREEN,'AddToHome：为方法或手动方法加主页快捷方式（直接/间接，最多12个，p.8）',
      ['users.state'],{'target':'M0001','direct':False})
def _c_sc_add(rt,a):
    _gate(rt); u=_user(rt.dev); _need(a['target']=='manual' or a['target'] in rt.methods,'NOT_FOUND')
    sc=[x for x in u['shortcuts'] if not (x['target']==a['target'] and x['direct']==a['direct'])]
    _need(len(sc)<MAX_SHORTCUTS,'MEMORY_FULL'); u['shortcuts']=sc+[dict(target=a['target'],direct=a['direct'])]
    return {'ok':True}


@term('shortcut.remove',obj({'target':dict(type='string',maxLength=10),'direct':BOOL}),OK_OUT,SCREEN,'User settings > Shortcuts：删除快捷方式',['users.state'],
      {'target':'M0001','direct':False})
def _c_sc_remove(rt,a):
    _gate(rt); u=_user(rt.dev); n=len(u['shortcuts'])
    u['shortcuts']=[x for x in u['shortcuts'] if not (x['target']==a['target'] and x['direct']==a['direct'])]; _need(len(u['shortcuts'])<n,'NOT_FOUND')
    return {'ok':True}


@term('manual.configure',obj({'values':obj({'type':{'type':'string','enum':['Melting point','Melting range']},'start_c':number('°C',20,350),
                                             'waiting_s':dict(type='integer',minimum=0,maximum=3600,**{'x-unit':'s'}),'rate_c_min':number('°C/min',.1,20),
                                             'end_c':number('°C',20,350)},optional=('type','start_c','waiting_s','rate_c_min','end_c'))}),OK_OUT,SCREEN,
      'Manual > Manual method：手动方法参数（保留为下次打开的设置，p.22）',['manual.method'],{'values':{'start_c':60}})
def _c_manual(rt,a):
    _gate(rt); new=dict(rt.dev['manual'],**a['values']); _need(new['start_c']<new['end_c'],'INVALID_ARGUMENT'); rt.dev['manual']=new
    return {'ok':True}


@term('manual.convert',obj({'title':NAME30}),obj({'method_id':TEXT}),SCREEN,'Manual method > Convert：把手动方法存为方法',['method.count','methods.catalog'],{'title':'From manual'})
def _c_convert(rt,a):
    m=rt.dev['manual']
    return _c_method_save(rt,dict(method_id=None,values=dict(title=a['title'],type=m['type'],start_c=m['start_c'],waiting_s=m['waiting_s'],rate_c_min=m['rate_c_min'],
                                                             end_c=m['end_c'],determination='Automatic',criterion='transmission',comment='',calibration=None)))


# ---- analyses (p.19-23, p.37)
COMMENTS=obj({'method':dict(type='string',maxLength=60),'analysis':dict(type='string',maxLength=60),'capillaries':dict(type='array',items=dict(type='string',maxLength=30),minItems=4,maxItems=4)},
             optional=('method','analysis','capillaries'))
@term('analysis.prepare',obj({'method_id':dict(type='string',maxLength=10),'comments':COMMENTS},optional=('comments',)),obj({'stage':TEXT}),SCREEN+['heater'],
      'Start analysis > Start：打开分析窗口并升温到起始温度（method_id=manual为手动方法；需要SD卡，p.19-21）',
      ['analysis.state','cycle.phase','cycle.stage','thermal.target_k','method.selected'],{'method_id':'M0001'})
def _c_prepare(rt,a):
    _gate(rt,idle=True); dev=rt.dev; mid=a['method_id']
    _need(rt.sd_inserted,'NO_SD_CARD')
    if mid=='manual':
        v=dev['manual']; params=dict(v,title='Manual method',determination='Automatic',criterion='transmission',comment='',calibration=None)
    else:
        _need(mid in rt.methods,'NOT_FOUND'); params=dict(dev['meta'].get(mid) or {})
        if not params:
            m=rt.methods[mid]; params=dict(title=mid,type='Melting point',start_c=m['start_k']-273.15,waiting_s=0,rate_c_min=m['ramp_k_s']*60,end_c=m['end_k']-273.15,
                                           determination='Automatic',criterion=m['criterion'],comment='',calibration=None)
    _need(not any(s.get('used') for s in rt.samples.values()),'PRECONDITION_FAILED')
    adj=dev['adjustment']; s=dev['settings']; expired=False
    if adj['time'] is not None and s['adj.expiry_action']!='None':
        expired=(_now(rt)-datetime.datetime.fromisoformat(adj['time'])).total_seconds()/86400>s['adj.validity_days']
        _need(not (expired and s['adj.expiry_action']=='Block'),'ADJUSTMENT_EXPIRED')
    c=a.get('comments',{})
    rt.analysis=dict(id=None,method_id=mid,params=params,stage='go_to_tstart',comments=dict(method=c.get('method',params.get('comment','')),analysis=c.get('analysis',''),
                     capillaries=list(c.get('capillaries',['']*4))),manual_points={},wait_until=None,result=None,adjustment_expired=expired)
    if mid!='manual': rt.selected=mid
    rt._operation_count+=1; rt.operation_id=f'{rt.instance_id}:measurement:{rt._operation_count}'
    rt.phase='running'; rt.stage='preheat'; rt.elapsed_s=0.; rt.target_k=params['start_c']+273.15; rt.furnace_on=True
    dev['last_start']=mid
    return {'stage':'go_to_tstart'}


@term('analysis.start',obj({}),obj({'stage':TEXT}),SCREEN+['heater','camera_sensor'],'分析窗口Start：T(start) reached后插入毛细管，开始等待时间与升温（p.20）',
      ['analysis.state','sample.loaded_count'],{})
def _c_astart(rt,a):
    _gate(rt); an=rt.analysis; _need(an is not None and an['stage']=='tstart_reached','PRECONDITION_FAILED'); _need(rt.samples,'NO_SAMPLES')
    for s in rt.samples.values(): s['used']=True
    an.update(stage='waiting',wait_until=rt.elapsed_s+an['params']['waiting_s'])
    return {'stage':'waiting'}


@term('analysis.set_point',obj({'channel':CHANNEL}),obj({'temperature_k':TEMP}),SCREEN+['camera_sensor'],'手动判定：毛细管熔化时按该毛细管的Set记录温度（p.20）',
      ['analysis.state'],{'channel':0})
def _c_set(rt,a):
    _gate(rt); an=rt.analysis; ch=int(a['channel'])
    _need(an is not None and an['params']['determination']=='Manual' and an['stage'] in ('waiting','ramp') and ch in rt.samples,'PRECONDITION_FAILED')
    _need(ch not in an['manual_points'],'PRECONDITION_FAILED')
    an['manual_points'][ch]=rt._indicated_k()
    return {'temperature_k':an['manual_points'][ch]}


@term('analysis.stop',obj({}),obj({'phase':PHASE}),SCREEN+['button_stop'],'Stop或Reset键并确认Yes：停止分析（随后询问是否保存结果，p.37）',['cycle.phase','analysis.state'],{})
def _c_astop(rt,a):
    _gate(rt,unlocked=False); an=rt.analysis; _need(an is not None and an['stage'] not in ('done','stopped','aborted'),'PRECONDITION_FAILED')
    rt.invoke_legacy('acquisition.stop'); an['stage']='stopped'
    if not rt.dev['settings']['seq.ask_save'] or rt.dev['locked']: _save_analysis(rt,True)
    return {'phase':rt.phase}


@term('analysis.save',obj({'save':BOOL}),OK_OUT,SCREEN,'Do you want to save the results? Yes（保存并按设置打印）/ No（不保存）',['results.analyses','analysis.state'],{'save':True})
def _c_asave(rt,a):
    _gate(rt); an=rt.analysis; _need(an is not None and an['stage']=='stopped','PRECONDITION_FAILED'); _save_analysis(rt,a['save'])
    return {'ok':True}


@term('analysis.comment',obj({'kind':{'type':'string','enum':['analysis','capillary']},'channel':CHANNEL,'text':dict(type='string',maxLength=60)},optional=('channel',)),OK_OUT,SCREEN,
      '分析窗口Analysis comment / Capillary comments',['analysis.state'],{'kind':'analysis','text':'batch 7'})
def _c_comment(rt,a):
    _gate(rt); an=rt.analysis; _need(an is not None,'PRECONDITION_FAILED')
    if a['kind']=='analysis': an['comments']['analysis']=a['text']
    else: an['comments']['capillaries'][int(a.get('channel',0))]=a['text']
    if an.get('id'):
        r=next((x for x in rt.dev['analyses'] if x['id']==an['id']),None)
        if r: r['comments']=dict(an['comments'],capillaries=list(an['comments']['capillaries']))
    return {'ok':True}


@term('analysis.close',obj({}),OK_OUT,SCREEN,'结果窗口Back：关闭已结束的分析',['analysis.state'])
def _c_aclose(rt,a):
    _gate(rt); _need(rt.analysis is None or rt.analysis['stage'] in ('done','aborted'),'BUSY'); rt.analysis=None
    return {'ok':True}


@term('result.set_status',obj({'analysis_id':dict(type='integer',minimum=1,**{'x-unit':'1'}),'channel':CHANNEL,'included':BOOL}),OK_OUT,SCREEN,
      'Results：毛细管状态included / excluded（参与平均值与s）',['results.analyses'],{'analysis_id':1,'channel':0,'included':False})
def _c_status(rt,a):
    _gate(rt); r=next((x for x in rt.dev['analyses'] if x['id']==a['analysis_id']),None); _need(r is not None,'NOT_FOUND')
    c=next((x for x in r['capillaries'] if x['channel']==int(a['channel'])),None); _need(c is not None,'NOT_FOUND')
    c['included']=a['included']; _stats(r)
    return {'ok':True}


def _stats(r):
    vals=[c['value_k'] for c in r['capillaries'] if c['included'] and c['value_k'] is not None]
    r['mean_k']=sum(vals)/len(vals) if vals else None
    r['sd_k']=math.sqrt(sum((v-r['mean_k'])**2 for v in vals)/(len(vals)-1)) if len(vals)>1 else None


def _save_analysis(rt,save):
    """Results of the analysis (p.20): per capillary the melting point (clear point; manual: the Set temperature) or the melting range; mean and s;
    a calibration method compares the mean with the substance's nominal value (p.41)."""
    an=rt.analysis; p=an['params']; dev=rt.dev
    caps=[]
    for ch,smp in sorted(rt.samples.items()):
        if p['determination']=='Manual': val=an['manual_points'].get(ch); onset=None
        else: val=smp.get('clear_k'); onset=smp.get('onset_k')
        caps.append(dict(channel=ch,value_k=val,onset_k=onset if p['type']=='Melting range' else None,included=val is not None,comment=an['comments']['capillaries'][ch]))
    r=dict(id=dev['next_analysis'],method_id=an['method_id'],title=p['title'],type=p['type'],time=_now(rt).isoformat(timespec='seconds'),
           user=dev['current_user'] or '-',capillaries=caps,comments=dict(an['comments'],capillaries=list(an['comments']['capillaries'])),
           stopped=an['stage']=='stopped',calibration=None)
    _stats(r)
    if p.get('calibration') and r['mean_k'] is not None:
        sub=next((x for x in dev['substances'] if x['name']==p['calibration']),None)
        if sub:
            dev_k=r['mean_k']-273.15-sub['nominal_c']
            r['calibration']=dict(substance=sub['name'],nominal_c=sub['nominal_c'],deviation_c=dev_k,passed=abs(dev_k)<=sub['tol_c']+1e-9)
            dev['calibrations']=(dev['calibrations']+[dict(r['calibration'],time=r['time'],mean_c=r['mean_k']-273.15)])[-50:]
    dev['next_analysis']+=1
    if save and dev['settings']['storage.sd']:
        dev['analyses']=(dev['analyses']+[r])[-50:]; an['id']=r['id']
    an['result']=r; an['stage']='done' if an['stage']!='stopped' else 'aborted'


@term('furnace.power_off',obj({}),OK_OUT,SCREEN+['heater'],'Manual > Furnace power off：关闭炉温控制，测量池无风扇自然冷却（无分析时，p.41）',['furnace.on'],{})
def _c_furnace_off(rt,a):
    _gate(rt,idle=True); _need(rt.furnace_on,'PRECONDITION_FAILED'); rt.furnace_on=False
    return {'ok':True}


# ---- setup: calibration substances, adjustment, maintenance (p.12-13, p.41)
@term('substance.save',obj({'name':NAME30,'nominal_c':number('°C',20,350),'tol_c':number('°C',0.05,5),'substance_id':dict(type='string',maxLength=40)},optional=('substance_id',)),
      OK_OUT,SCREEN,'Setup > Calibration substances：新增或修改校准物质（名称、标称熔点、容差）',['calibration.substances'],{'name':'Vanillin','nominal_c':81.7,'tol_c':0.5})
def _c_sub_save(rt,a):
    _gate(rt); subs=rt.dev['substances']; old=next((x for x in subs if x['name']==a['name']),None)
    if old is None: _need(len(subs)<20,'MEMORY_FULL'); subs.append(dict(name=a['name'],substance_id=a.get('substance_id',''),nominal_c=a['nominal_c'],tol_c=a['tol_c']))
    else: old.update(nominal_c=a['nominal_c'],tol_c=a['tol_c'])
    return {'ok':True}


@term('substance.delete',obj({'name':NAME30}),OK_OUT,SCREEN,'删除校准物质（方法引用中的不能删）',['calibration.substances'],{'name':'Vanillin'})
def _c_sub_delete(rt,a):
    _gate(rt); _need(any(x['name']==a['name'] for x in rt.dev['substances']),'NOT_FOUND')
    _need(not any(v.get('calibration')==a['name'] for v in rt.dev['meta'].values()),'IN_USE')
    rt.dev['substances']=[x for x in rt.dev['substances'] if x['name']!=a['name']]
    return {'ok':True}


@term('adjustment.perform',obj({}),obj({'a':number('1'),'b':number('°C')}),SCREEN+['temperature_sensor'],
      'Setup > Adjustment：用最近的不同校准物质的校准结果（至少两种）求温度线性修正并存储（读数与炉温控制按修正）',['adjustment.state','thermal.indicated_temperature_k'],{})
def _c_adjust(rt,a):
    _gate(rt,idle=True); dev=rt.dev; latest={}
    for c in dev['calibrations']: latest[c['substance']]=c
    pts=sorted(((c['mean_c']-dev['adjustment']['b'])/dev['adjustment']['a'],c['nominal_c']) for c in latest.values())
    _need(len(pts)>=2 and pts[-1][0]-pts[0][0]>=10.0,'NO_CALIBRATION')
    n=len(pts); mx=sum(p[0] for p in pts)/n; my=sum(p[1] for p in pts)/n
    a_=sum((x-mx)*(y-my) for x,y in pts)/sum((x-mx)**2 for x,_ in pts); b_=my-a_*mx
    _need(0.9<=a_<=1.1 and abs(b_)<=10.0,'ADJUSTMENT_OUT_OF_RANGE')
    dev['adjustment']=dict(time=_now(rt).isoformat(timespec='seconds'),a=a_,b=b_,points=[dict(raw_c=x,nominal_c=y) for x,y in pts])
    return {'a':a_,'b':b_}


@term('data.export',obj({}),obj({'file':TEXT}),SCREEN+['sd_port'],'Mainten. & Service > Import / Export：导出方法与设置到SD卡',['sd_card.state'],{})
def _c_export(rt,a):
    _gate(rt); _need(rt.sd_inserted,'NO_SD_CARD')
    name=f"MP70_export_{_now(rt).strftime('%Y%m%d_%H%M%S')}.zip"; rt.dev['sd_files']=(rt.dev['sd_files']+[name])[-100:]
    return {'file':name}


@term('data.delete_sd',obj({}),OK_OUT,SCREEN+['sd_port'],'Mainten. & Service > Delete data on SD card：删除SD卡上的结果与文件',['sd_card.state','results.analyses'],{})
def _c_delete_sd(rt,a):
    _gate(rt,idle=True); _need(rt.sd_inserted,'NO_SD_CARD'); rt.dev['sd_files']=[]; rt.dev['analyses']=[]
    return {'ok':True}


@term('system.reset',obj({}),OK_OUT,SCREEN,'Mainten. & Service > Reset to factory settings：方法、设置、用户、结果恢复出厂（时钟与调整保留）',
      ['device.settings','method.count','methods.catalog','users.state','results.analyses'],{})
def _c_reset(rt,a):
    _gate(rt,idle=True); keep=dict(clock_offset_s=rt.dev['clock_offset_s'],adjustment=rt.dev['adjustment'],power_epoch=rt.dev['power_epoch'])
    rt.dev=_mp_new_device(); rt.dev.update(copy.deepcopy(keep)); rt.methods={}; rt.selected=''
    return {'ok':True}


# ---- host (evaluator): capillaries inserted at T(start), SD card, sensor errors
@term('capillary.insert',CAPS['sample.configure'][0],obj({'configured':BOOL}),['capillary_0','capillary_1','capillary_2','capillary_3'],
      '受信任宿主：在T(start) reached时插入装好样品的毛细管（室温毛细管进入热炉，p.20）',['sample.input_kg','sample.retained_kg','sample.loaded_count'],
      CAPS['sample.configure'][6],kind='configuration',visibility='evaluator')
def _c_insert(rt,a):
    an=rt.analysis; _need(an is not None and an['stage']=='tstart_reached','PRECONDITION_FAILED')
    t=rt.templates.get(a['substance_id'])
    _need(t and t['runtime_eligible'] and a['channel'] not in rt.samples and rt.temperature_k<t['value']-1,'PRECONDITION_FAILED')
    if a['decomposition_onset_k'] is not None: _need(a['decomposition_source'].strip(),'INVALID_ARGUMENT')
    smp=copy.deepcopy(a); smp.update(temperature_k=293.15,melt_fraction=0.,degraded=0.,onset_k=None,clear_k=None,transmission=.05,curve=[],truncated=False,used=False)
    rt.samples[int(a['channel'])]=smp; rt.input_kg+=a['mass_kg']
    return {'configured':True}


@term('sd_card.set',obj({'inserted':BOOL}),obj({'inserted':BOOL}),['sd_port'],'受信任宿主：插入/取出SD卡（运行分析需要SD卡，p.19）',['sd_card.state'],{'inserted':True},
      kind='configuration',visibility='evaluator')
def _c_sd(rt,a):
    _need(not (rt.phase=='running' and not a['inserted']),'BUSY'); rt.sd_inserted=a['inserted']
    return {'inserted':rt.sd_inserted}


@term('sensor.configure_error',obj({'offset_k':number('K',-10,10),'gain':number('1',-.05,.05)},optional=('offset_k','gain')),obj({'errors':{'type':'object'}}),
      ['temperature_sensor'],'评测者：炉温传感器误差（偏置/增益），默认0（理想）；Adjustment按校准结果修正',['sensor.errors','thermal.indicated_temperature_k'],{'offset_k':1.0},
      kind='configuration',visibility='evaluator')
def _c_err(rt,a):
    rt.sensor.update(a)
    return {'errors':dict(rt.sensor)}
# <<< CHEM-006 MP70 terminal

RUN_GUARDED=('acquisition.stop','analysis.stop','analysis.save','analysis.start','analysis.set_point','analysis.comment','power.shutdown')   # another agent's analysis
# (power.set is the mains supply at the power inlet, a hard power cut: never refused, the run is cancelled as on a power loss; 1.2.1, lead rule 10-19)
ACTIVITY=('panel.touch','panel.press')                                                   # gestures that keep the screen saver off (with terminal commands)
_PANELS=weakref.WeakKeyDictionary()                                                      # one PanelUI per runtime, kept out of __dict__
class MtMp70Runtime:
    def __init__(self,instance_id='mp70_01'):
        self.instance_id=instance_id; self.time_s=0.; self.revision=0; self.powered=False; self.temperature_k=293.15; self.target_k=293.15
        self.methods={}; self.selected=''; self.samples={}; self.input_kg=0.; self.removed_kg=0.
        self.phase='idle'; self.stage='idle'; self.elapsed_s=0.; self.operation_id=None; self._operation_count=0
        self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances']
        self._principals={'__public_agent__':'agent'}; self._requests={}; self._owners={}
        # terminal layer (1.1.0)
        self.dev=_mp_new_device(); self.analysis=None; self.furnace_on=True; self.sd_inserted=True; self.screensaver=False; self.boot_until=0.
        self.sensor=dict(offset_k=0.,gain=0.); self.panel_capture_dir=None
        self._caller=None; self.last_activity_s=0.                                       # session principal of the call in progress; last operator action
    @property
    def panel(self):
        p=_PANELS.get(self)
        if p is None: p=_PANELS[self]=_panel_ui.PanelUI(PANEL_SPEC,self)
        return p
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','evaluator','debug'): raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals: raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals: raise ActionRejected('ACCESS_DENIED')
        return MtMp70Session(self,principal_id)
    def describe(self): return self.session().describe(instance_id=self.instance_id)
    # ---- furnace sensor: ideal by default; the stored adjustment corrects the reading the display and the control use
    def _sensor_c(self,true_k=None):
        adj=self.dev['adjustment']; t=(self.temperature_k if true_k is None else true_k)-273.15
        return adj['a']*(t*(1+self.sensor['gain'])+self.sensor['offset_k'])+adj['b']
    def _indicated_k(self): return round(self._sensor_c(),2)+273.15
    def _control_k(self,target_k):
        adj=self.dev['adjustment']; raw=(target_k-273.15-adj['b'])/adj['a']
        return (raw-self.sensor['offset_k'])/(1+self.sensor['gain'])+273.15
    def _active(self):
        """Method of the running measurement: a terminal analysis (stored or manual method) or the selected stored method."""
        if self.analysis is not None and self.analysis['stage'] not in ('done','stopped','aborted'):
            p=self.analysis['params']; return dict(start_k=p['start_c']+273.15,end_k=p['end_c']+273.15,ramp_k_s=p['rate_c_min']/60.0,criterion=p['criterion'])
        return self.methods[self.selected]
    def _values(self):
        r={'power.enabled':self.powered,'thermal.target_k':self.target_k,'thermal.temperature_k':self.temperature_k,'thermal.indicated_temperature_k':self._indicated_k(),'method.count':len(self.methods),'method.selected':self.selected,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.elapsed_s':self.elapsed_s,'sample.loaded_count':len(self.samples),'sample.input_kg':self.input_kg,'sample.retained_kg':sum(s['mass_kg'] for s in self.samples.values()),'sample.removed_kg':self.removed_kg,'sample.degraded_kg':sum(s['mass_kg']*s['degraded'] for s in self.samples.values())}
        for i in range(4):
            s=self.samples.get(i,{})
            for k in ['onset_k','clear_k','transmission']: r[f'channel_{i}.{k}']=s.get(k)
        return r
    def _terminal_values(self):
        dev=self.dev; an=self.analysis
        users=dict(current=dev['current_user'],locked=dev['locked'],booting=self.powered and self.time_s<self.boot_until-1e-9,
                   users=[dict(user_name=u['user_name'],full_name=u['full_name'],active=u['active'],has_password=u['password'] is not None,
                               shortcuts=copy.deepcopy(u['shortcuts'])) for u in dev['users']])
        u=_user(dev)
        astate=None
        if an is not None:
            p=an['params']; m=self._active() if an['stage'] not in ('done','stopped','aborted') else None
            remaining=None
            if m is not None:
                wait=max(0.,(an['wait_until']-self.elapsed_s)) if an['stage']=='waiting' else p['waiting_s'] if an['stage'] in ('go_to_tstart','tstart_reached') else 0.
                remaining=wait+max(0.,(m['end_k']-max(self.target_k,m['start_k']))/m['ramp_k_s'])
            caps=[]
            for ch in range(4):
                smp=self.samples.get(ch)
                caps.append(dict(channel=ch,loaded=smp is not None,onset_k=smp.get('onset_k') if smp else None,clear_k=smp.get('clear_k') if smp else None,
                                 manual_k=an['manual_points'].get(ch),transmission=smp.get('transmission') if smp else None))
            vals=[c['manual_k'] if p['determination']=='Manual' else c['clear_k'] for c in caps if c['loaded']]
            vals=[v for v in vals if v is not None]
            mean=sum(vals)/len(vals) if vals else None
            sd=math.sqrt(sum((v-mean)**2 for v in vals)/(len(vals)-1)) if len(vals)>1 else None
            astate=dict(method_id=an['method_id'],operation_id=self.operation_id,adjustment_expired=bool(an.get('adjustment_expired')),
                        title=p['title'],type=p['type'],determination=p['determination'],stage=an['stage'],comments=copy.deepcopy(an['comments']),
                        remaining_s=remaining,capillaries=caps,mean_k=mean,sd_k=sd,result_id=an.get('id'),result=copy.deepcopy(an['result']),params=copy.deepcopy(p))
        return {'device.clock':_now(self).isoformat(timespec='seconds'),'device.settings':copy.deepcopy(dev['settings']),'users.state':users,
                'user.preferences':copy.deepcopy(u['prefs']) if u else _defaults(MP_PREF_FIELDS),
                'methods.catalog':[dict(method_id=k,**(dev['meta'].get(k) or dict(title=k,type='Melting point',start_c=v['start_k']-273.15,waiting_s=0,
                                                                                       rate_c_min=v['ramp_k_s']*60,end_c=v['end_k']-273.15,determination='Automatic',
                                                                                       criterion=v['criterion'],comment='',calibration=None)))
                                   for k,v in sorted(self.methods.items())],
                'manual.method':dict(dev['manual']),'analysis.state':astate,'results.analyses':copy.deepcopy(dev['analyses']),
                'calibration.substances':copy.deepcopy(dev['substances']),'calibration.history':copy.deepcopy(dev['calibrations']),
                'adjustment.state':copy.deepcopy(dev['adjustment']),'sd_card.state':dict(inserted=self.sd_inserted,files=list(dev['sd_files'])),
                'furnace.on':self.furnace_on,'display.screensaver':self.screensaver,'sensor.errors':dict(self.sensor)}
    def read(self,visibility='agent'):
        vals=dict(self._values(),**self._terminal_values())
        return copy.deepcopy({k:v for k,v in vals.items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
    def debug_snapshot(self): return dict(state=self.read('evaluator'),samples=copy.deepcopy(self.samples),methods=copy.deepcopy(self.methods))
    def _measure(self,channel=0):
        s=self.samples.get(channel,{})
        return dict(channel=channel,onset_k=s.get('onset_k'),clear_k=s.get('clear_k'),transmission=s.get('transmission'),phase=self.phase,quality='unavailable' if not s else ('suspect_decomposition' if s.get('degraded',0)>.01 else 'estimated_decomposition_unknown' if s.get('decomposition_onset_k') is None else 'estimated'),evidence='synthetic',template_source=TEMPLATE_REL,video_status='not_implemented')
    def _check_caller(self,capability,a):
        """An agent may not stop, finish, save or change another agent's running analysis (direct call or panel gesture alike: the session sets
        _caller around both)."""
        who=self._caller
        if who is None or self._principals.get(who)!='agent' or self.phase!='running' or capability not in RUN_GUARDED: return
        owner=self._owners.get(self.operation_id)
        if owner is None or owner==who: return
        raise ActionRejected('ACCESS_DENIED')
    def _saver_tick(self):
        """Screen saver after the user's delay without operator actions (User settings > Screen); not while an analysis runs, and the delay
        restarts at the end of an analysis (assumptions: the manual does not describe the timing)."""
        if not self.powered or self.screensaver or self.time_s<self.boot_until-1e-9 or self.phase=='running': return
        u=_user(self.dev); delay=(u['prefs'] if u else _defaults(MP_PREF_FIELDS))['screensaver_min']*60.0
        if self.time_s-max(self.last_activity_s,self.boot_until)>=delay-1e-9: self.screensaver=True
    def invoke(self,capability,**a):
        if capability not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        self._check_caller(capability,a)
        if capability in TERMINAL or capability in PANEL_CAPS:
            _tvalidate(CAPS[capability][0],a); return self._terminal(capability,a)
        return self.invoke_legacy(capability,**a)
    def invoke_legacy(self,capability,**a):
        validate(CAPS[capability][0],a)
        if capability not in ('power.set','acquisition.stop','melting.measure','melting.curve') and self.phase=='running': raise ActionRejected('BUSY')
        if capability=='power.set':
            was=self.powered; self.powered=a['enabled']
            if not self.powered and self.phase=='running': self.phase='cancelled'; self.stage='hold'; self.target_k=293.15
            self._power_changed(was)
            r=dict(a)
        elif capability=='thermal.set_temperature': self.target_k=a['temperature_k']; self.furnace_on=True; r=dict(a)
        elif capability=='method.configure':
            if not a['method_id'].strip() or a['start_k']>=a['end_k']: raise ActionRejected('INVALID_ARGUMENT')
            if a['method_id'] not in self.methods and len(self.methods)>=20: raise _rejected('PRECONDITION_FAILED','all 20 method slots are in use; overwrite an existing method_id')
            self.methods[a['method_id']]=copy.deepcopy(a); r={'stored_count':len(self.methods)}
        elif capability=='method.select':
            if a['method_id'] not in self.methods: raise _rejected('PRECONDITION_FAILED','no stored method %r (method.configure first)'%a['method_id'])
            self.selected=a['method_id']; r=dict(a)
        elif capability=='sample.configure':
            t=self.templates.get(a['substance_id'])
            why=('substance %r has no runtime melting-point reference'%a['substance_id'] if not t or not t['runtime_eligible'] else 'capillary channel %d is occupied'%a['channel'] if a['channel'] in self.samples else
                 'the furnace is above 50 C (%.2f K); let it cool before inserting capillaries'%self.temperature_k if self.temperature_k>323.15 else
                 'the furnace (%.2f K) is within 1 K of the melting point of the substance'%self.temperature_k if self.temperature_k>=t['value']-1 else None)
            if why: raise _rejected('PRECONDITION_FAILED',why)   # 1.2.2: the message names the cause
            if a['decomposition_onset_k'] is not None and not a['decomposition_source'].strip(): raise ActionRejected('INVALID_ARGUMENT')
            s=copy.deepcopy(a); s.update(temperature_k=self.temperature_k,melt_fraction=0.,degraded=0.,onset_k=None,clear_k=None,transmission=.05,curve=[],truncated=False,used=False)
            self.samples[int(a['channel'])]=s; self.input_kg+=a['mass_kg']; r={'configured':True}
        elif capability=='sample.unload':
            if a['channel'] not in self.samples or self.temperature_k>323.15:
                raise _rejected('PRECONDITION_FAILED','capillary channel %d is empty'%a['channel'] if a['channel'] not in self.samples else 'the furnace is above 50 C (%.2f K); let it cool first'%self.temperature_k)
            self.removed_kg+=self.samples.pop(a['channel'])['mass_kg']; r={'unloaded':True}
        elif capability=='acquisition.start':
            why=('the instrument is switched off' if not self.powered else 'no method is selected (method.select)' if self.selected not in self.methods else
                 'no capillary is inserted' if not self.samples else 'a capillary has already been measured; replace it (sample.unload, then a fresh one)' if any(s['used'] for s in self.samples.values()) else None)
            if why: raise _rejected('PRECONDITION_FAILED',why)   # 1.2.2: the message names the cause
            m=self.methods[self.selected]
            if any(m['start_k']>=self.templates[s['substance_id']]['value']-1 for s in self.samples.values()):
                raise _rejected('PRECONDITION_FAILED','the method start temperature %.2f K is not at least 1 K below the melting point of every sample'%m['start_k'])
            self._operation_count+=1; self.operation_id=f'{self.instance_id}:measurement:{self._operation_count}'; self.phase='running'; self.stage='preheat'; self.elapsed_s=0.; self.target_k=m['start_k']
            self.analysis=None; self.furnace_on=True
            for s in self.samples.values(): s['used']=True
            r={'phase':self.phase,'operation_id':self.operation_id}
        elif capability=='acquisition.stop':
            if self.phase=='running': self.phase='cancelled'; self.stage='hold'; self.target_k=self.temperature_k
            r={'phase':self.phase}
        elif capability=='melting.measure': return self._measure(a['channel'])
        else:
            s=self.samples.get(a['channel'],{}); return dict(channel=a['channel'],points=copy.deepcopy(s.get('curve',[])),evidence='synthetic',template_source=TEMPLATE_REL,sample_interval_s=1.,truncated=s.get('truncated',False))
        self.revision+=1; return copy.deepcopy(r)
    def _power_changed(self,was):
        """Start-up (p.15: 'Starting up the system', then the home screen or the Login window) and shut-down (logged out)."""
        dev=self.dev
        if not was and self.powered:
            self.boot_until=self.time_s+BOOT_S; dev['power_epoch']+=1; s=dev['settings']; self.screensaver=False; dev['locked']=False
            start=s['policy.startup_user'] if s['policy.startup'] else None
            dev['current_user']=start if start and _user(dev,start) and _user(dev,start)['active'] and _user(dev,start)['password'] is None else None
        if was and not self.powered:
            dev['current_user']=None; dev['locked']=False; self.screensaver=False
            if self.analysis is not None and self.analysis['stage'] not in ('done','stopped','aborted'): self.analysis['stage']='aborted'
    def _terminal(self,capability,a):
        keep=('templates','_principals','_requests','_owners')
        old=copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in keep})
        try:
            r=TERMINAL[capability](self,a) if capability in TERMINAL else self._panel_cmd(capability,a)
        except Exception:
            for k in list(self.__dict__):
                if k not in keep: del self.__dict__[k]
            self.__dict__.update(old); raise
        if CAPS[capability][4]!='query': self.revision+=1
        if capability in TERMINAL or capability in ACTIVITY: self.last_activity_s=self.time_s
        return copy.deepcopy(r)
    def _analysis_tick(self):
        """Stages of a terminal analysis (p.9, p.20): Go to T(start) -> T(start) reached (insert capillaries, Start) -> Waiting time -> Ramp ->
        results (Create report)."""
        an=self.analysis
        if an is None or self.phase!='running': return
        if an['stage']=='go_to_tstart' and abs(self._sensor_c()+273.15-self.target_k)<.1: an['stage']='tstart_reached'
        elif an['stage']=='waiting' and self.elapsed_s>=an['wait_until']-1e-9: an['stage']='ramp'
    def step(self,dt_s):
        if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0: raise ValueError('finite positive dt_s required')
        left=float(dt_s)
        while left>1e-10:
            # Host-aligned integration; sample output on exact elapsed integer seconds.
            active=self.phase=='running'; h=min(left,1.)
            u=_user(self.dev); delay=(u['prefs'] if u else _defaults(MP_PREF_FIELDS))['screensaver_min']*60.0
            due=max(self.last_activity_s,self.boot_until)+delay-self.time_s
            if self.powered and not self.screensaver and not active and due>1e-9: h=min(h,due)
            if active: h=min(h,math.floor(self.elapsed_s+1e-8)+1-self.elapsed_s)
            if active and self.stage=='ramp':
                m=self._active(); h=min(h,max((m['end_k']-self.target_k)/m['ramp_k_s'],1e-8)); self.target_k=min(m['end_k'],self.target_k+m['ramp_k_s']*h)
            target=self._control_k(self.target_k) if self.powered and self.furnace_on else 293.15
            # At 20 C ambient, 150*ln((350-20)/(50-20)) ~= 359.7 s, not fixed 330 s.
            tau=150. if target<self.temperature_k else 15.
            self.temperature_k=target+(self.temperature_k-target)*math.exp(-h/tau)
            if active: self.elapsed_s+=h
            # Thermal/phase changes continue after acquisition cancellation; only recording stops.
            for s in self.samples.values():
                tau_s=1.+2.*s['particle_size_m']/50e-6
                s['temperature_k']=self.temperature_k+(s['temperature_k']-self.temperature_k)*math.exp(-h/tau_s)
                center=self.templates[s['substance_id']]['value']-10*(1-s['purity']); width=.25+5*(1-s['purity'])+s['particle_size_m']/1e-3
                fraction=1/(1+math.exp(max(-60,min(60,-(s['temperature_k']-center)/width))))
                s['melt_fraction']=max(s['melt_fraction'],fraction)
                if s['decomposition_onset_k'] is not None and s['temperature_k']>=s['decomposition_onset_k']: s['degraded']=1-(1-s['degraded'])*math.exp(-h/30.)
                s['transmission']=max(0.,min(1.,(.05+.9*s['melt_fraction'])*(1-.85*s['degraded'])))
                criterion=s['melt_fraction'] if active and self._active()['criterion']=='visual_proxy' else (s['transmission']-.05)/.9
                if active and self.stage=='ramp':
                    indicated=self._indicated_k()
                    if criterion>=.1 and s['onset_k'] is None: s['onset_k']=indicated
                    if criterion>=.9 and s['clear_k'] is None: s['clear_k']=indicated
                if active and abs(self.elapsed_s-round(self.elapsed_s))<1e-7:
                    if len(s['curve'])<20000: s['curve'].append(dict(time_s=round(self.elapsed_s,6),temperature_k=self._indicated_k(),transmission=s['transmission']))
                    else: s['truncated']=True
            if active:
                self._analysis_tick()
                released=self.analysis is None or self.analysis['stage']=='ramp'
                if self.stage=='preheat' and abs(self._sensor_c()+273.15-self.target_k)<.1 and released: self.stage='ramp'
                if self.stage=='ramp' and self.target_k>=self._active()['end_k']-1e-8:
                    self.phase='succeeded'; self.stage='hold'
                    if self.analysis is not None and self.analysis['stage']=='ramp': _save_analysis(self,True)
            self.time_s+=h; left-=h
            if active and self.phase!='running': self.last_activity_s=self.time_s          # the end of a task restarts the screen-saver delay
            self._saver_tick()
        self.revision+=1; return self.read('evaluator')
    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        out=self.read('agent'); dev=self.dev; u=_user(dev)
        cat={m['method_id']:m for m in out['methods.catalog']}
        out.update({'host.powered':self.powered,'host.power_epoch':dev['power_epoch'],
                    'host.shortcuts':[dict(x,label=('Manual method' if x['target']=='manual' else cat.get(x['target'],{}).get('title',x['target'])),
                                           kind='direct' if x['direct'] else 'indirect') for x in (u['shortcuts'] if u else [])],
                    'host.task':self.phase=='running','host.temperature_c':round(self._sensor_c(),1),
                    'host.last_result':copy.deepcopy(dev['analyses'][-1]) if dev['analyses'] else None,'host.last_start':dev['last_start'],
                    'host.analyses_desc':[dict(id=r['id'],title=r['title'],time=r['time']) for r in reversed(dev['analyses'])]})
        stats={}
        for r in dev['analyses']:
            v=[c['value_k']-273.15 for c in r['capillaries'] if c['included'] and c['value_k'] is not None]
            stats[str(r['id'])]=dict(n=len(v),mean=_fmt_dec(sum(v)/len(v),2) if v else '-',sd=_fmt_dec(r['sd_k'],3) if r['sd_k'] is not None else '-',
                                    range=f'{_fmt_dec(min(v),1)} / {_fmt_dec(max(v),1)}' if v else '-')
        curves={str(ch):[[c['time_s'],c['transmission']*100.0] for c in smp['curve']] for ch,smp in self.samples.items()}
        out.update({'host.result_stats':stats,'host.curves':curves,'host.curve_span':max(60.0,self.elapsed_s)})
        out.update(self._furnace_images())
        return out
    def _furnace_images(self):
        """Furnace inside view (p.42): the reflected-light camera picture of the 4 capillaries and the transmitted-light image of the 4 holes, drawn
        from the capillary model (grey levels): powder reflects white and clears as it melts; a hole transmits through an empty slot or the sample's
        transmission (assumed geometry and grey levels; no clogged holes)."""
        W,H,X=64,40,(9,23,37,51)
        refl=[[12]*W for _ in range(H)]; trans=[[18]*W for _ in range(8)]
        for ch,x in enumerate(X):
            smp=self.samples.get(ch)
            for y in range(2,7):
                for dx in range(-1,3): trans[y-1][x+dx]=int(round(255*(smp['transmission'] if smp else .95)))
            if smp is None: continue
            powder=int(round((40+190*(1-smp['melt_fraction']))*(1-.6*smp['degraded'])))
            for y in range(3,H-3):
                for dx in (-1,0,1,2):
                    refl[y][x+dx]=70 if dx in (-1,2) or y<H-15 else powder                    # glass wall / empty tube above, sample column below
        return {'host.furnace_reflected':refl,'host.furnace_transmitted':trans}
    def panel_invoke(self,capability_id,arguments):
        try:
            self._check_caller(capability_id,arguments)
            if capability_id in TERMINAL:
                _tvalidate(CAPS[capability_id][0],arguments)
                keep=('templates','_principals','_requests','_owners')
                old=copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in keep})
                try: return TERMINAL[capability_id](self,arguments)
                except Exception:
                    for k in list(self.__dict__):
                        if k not in keep: del self.__dict__[k]
                    self.__dict__.update(old); raise
            return self.invoke_legacy(capability_id,**arguments)
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None
    def panel_time(self): return self.time_s
    def _panel_cmd(self,capability,a):
        try:
            if capability=='panel.press': return dict(accepted=bool(self.panel.press(a['input_id'],float(a.get('duration_s',.1)))))
            if capability=='panel.touch':
                if ('to_x' in a)!=('to_y' in a): raise ActionRejected('INVALID_ARGUMENT')
                return dict(accepted=bool(self.panel.touch(a['display_id'],a['x'],a['y'],float(a.get('duration_s',.1)),(a['to_x'],a['to_y']) if 'to_x' in a else None)))
            if capability=='panel.capture':
                data,w,h=self.panel.png_bytes(a['display_id']); sha=hashlib.sha256(data).hexdigest()
                d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id
                d.mkdir(parents=True,exist_ok=True); path=d/f"{a['display_id']}_{sha[:16]}.png"
                if not path.exists(): path.write_bytes(data)
                return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
            return dict(frame=self.panel.frame())
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None
class MtMp70Session:
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
        meta=export_mt_mp70_contract_metadata()
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
        before=h.operation_id; h._caller=self._principal
        try: result=h.invoke(capability_id,**arguments)
        finally: h._caller=None
        op=result.get('operation_id')
        if op: h._owners[op]=self._principal
        elif h.operation_id and h.operation_id!=before: h._owners[h.operation_id]=self._principal   # an analysis started on the terminal (analysis.prepare)
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
ANY={'type':'object'}; NULL_OBJ={'type':['object','null']}; SLOT_ZH={}
def _slot(k,schema,unit,origin,vis,components,meaning):
    SLOTS[k]=(schema,unit,origin,vis,components,None); SLOT_ZH[k]=meaning
_slot('device.clock',TEXT,'1','setpoint','agent',['screen'],'终端时钟（ISO本地时间）')
_slot('device.settings',ANY,'1','setpoint','agent',['screen'],'Setup设置（标识、日期时间格式、页眉页脚、数据存储、账户策略、分析行为、打印机、网络、网络存储）')
_slot('users.state',ANY,'1','setpoint','agent',['screen'],'用户：当前用户、锁定、启动中、用户列表（无密码）与快捷方式')
_slot('user.preferences',ANY,'1','setpoint','agent',['screen'],'当前用户的User settings')
_slot('methods.catalog',dict(type='array',items=ANY),'1','setpoint','agent',['display_digits'],'方法目录（ID、标题、类型、温度程序、判定、校准物质、注释）')
_slot('manual.method',ANY,'1','setpoint','agent',['display_digits'],'上次手动方法的参数')
_slot('analysis.state',NULL_OBJ,'1','derived','agent',['screen','display_digits'],'分析窗口：阶段（Go to T(start) / T(start) reached / Waiting time / Ramp / 结果）、剩余时间、各毛细管值、平均值与s、注释、operation_id（可poll/cancel）、调整数据过期警告')
_slot('results.analyses',dict(type='array',items=ANY),'1','derived','agent',['screen'],'SD卡上保存的分析结果（最近50个）')
_slot('calibration.substances',dict(type='array',items=ANY),'1','setpoint','agent',['screen'],'校准物质（名称、标称熔点、容差）')
_slot('calibration.history',dict(type='array',items=ANY),'1','derived','agent',['screen'],'校准分析结果（与标称值的偏差、是否在容差内）')
_slot('adjustment.state',ANY,'1','setpoint','agent',['temperature_sensor'],'温度调整：时间、线性修正与所用校准点')
_slot('sd_card.state',ANY,'1','scene','agent',['sd_port'],'SD卡是否插入与导出文件')
_slot('furnace.on',BOOL,'1','setpoint','agent',['heater'],'炉温控制开（Furnace power off后为关）')
_slot('display.screensaver',BOOL,'1','setpoint','agent',['screen','power_led'],'屏保（电源灯渐亮渐暗）')
_slot('sensor.errors',ANY,'1','process_truth','evaluator',['temperature_sensor'],'炉温传感器误差参数（仿真用，默认0）')

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
_panel_ui=_panel_engine()
_BUILD=os.environ.get('MP70_PANEL_BUILD')=='1'
PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS)) if (ASSET_DIR/'panel_ui.json').exists() and not _BUILD else None
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}
_PANEL_FX=sorted({e for c in (_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []) for e in CAPS[c][7]})
for _k,_d in PANEL_CAPS.items():
    CAPS[_k]=(_d['input_schema'],_d['output_schema'],_d['component_ids'],_d['function_zh'],_d['kind'],_d['visibility'],_d['example_arguments'],[] if _d['kind']=='query' else _PANEL_FX)
ERRORS_ZH={'POWER_OFF':'仪器已关机','STARTING_UP':'正在启动（Starting up the system）','LOGIN_REQUIRED':'未登录','LOCKED':'仪器已锁定（只有锁定用户能解锁）',
           'WRONG_PASSWORD':'用户名或密码错误','NO_PASSWORD':'没有密码的用户不能锁定仪器','NOT_FOUND':'条目不存在','NAME_EXISTS':'名称已存在','MEMORY_FULL':'条目数已达上限',
           'IN_USE':'条目正在使用或不能删除','NO_SD_CARD':'未插入SD卡','NO_SAMPLES':'没有插入毛细管','ADJUSTMENT_EXPIRED':'调整数据已过期（设置为Block）',
           'NO_CALIBRATION':'缺少至少两种校准物质（相差≥10 °C）的校准结果','ADJUSTMENT_OUT_OF_RANGE':'调整超出合理范围（斜率0.9–1.1，偏置≤10 °C）'}

def export_mt_mp70_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=SLOT_ZH.get(key),origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'MtMp70Session.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电、方法存在且有未使用固体样品；初温低于文献熔点',['power.enabled','method.selected','sample.loaded_count','cycle.phase'],'powered and selected in methods and samples and all not used and start < reference-1'),
         'sample.configure':('宿主授权、槽为空、炉温不高于50C且低于样品熔点',['thermal.indicated_temperature_k','cycle.phase'],'not running and channel empty and eligible reference and T<=323.15 and T<reference-1'),
         'sample.unload':('宿主授权、已冷却、槽有样品',['thermal.indicated_temperature_k','cycle.phase'],'not running and T<=323.15 and channel loaded'),
         'method.configure':('运行中锁定；最多20方法，初温低于终温',['cycle.phase','method.count'],'not running and start<end and (existing id or count<20)'),
         'method.select':('运行中锁定且方法存在',['cycle.phase','method.count'],'not running and id in methods'),
         'thermal.set_temperature':('运行中锁定温度目标',['cycle.phase'],'not running')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        elif key in TERMINAL and vis=='agent':
            pre=[dict(description_zh='仪器开机且启动完成、已登录且未锁定（Stop与登录除外）；编辑与新分析需无运行任务',state_ids=['power.enabled','users.state','cycle.phase'],
                      enforced_by='runtime',check='_gate(rt, login, unlocked, idle)')]
        elif key.startswith('panel.') and kind!='query':
            pre=[dict(description_zh='面板按当前屏幕与手册规则处理手势；关机时只有电源键起作用；锁定时只有Reset键与解锁登录起作用；被调用命令的前提同样适用',
                      state_ids=['power.enabled','users.state'],enforced_by='runtime',check='PanelUI gesture dispatch (panel_ui.json)')]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.mt_mp70.MtMp70Session.invoke',invocation=f'session.invoke(instance_id="mp70_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；取消保留温度、相态、分解和曲线' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经发生的熔化和分解。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=ERRORS_ZH.get(e,e) if key in TERMINAL else e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']+(list(ERRORS_ZH) if key in TERMINAL else [])],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['heater','temperature_sensor','led_source','camera_sensor','capillary_0','capillary_1','capillary_2','capillary_3'],
      state_ids=['thermal.temperature_k','thermal.target_k','sample.retained_kg','sample.input_kg','sample.removed_kg','sample.degraded_kg'],
      meaning_zh='文献熔点参考驱动四路毛细管热滞后、纯度/颗粒展宽与合成透光，质量账本守恒',implementation='lumped',
      equations=['T_next=T_target+(T-T_target)*exp(-dt/tau); tau_heating=15s, tau_cooling=150s',
      'ramp_target_next=min(end,target+rate*dt)',
      'Tm_proxy=Tm_reference-10*(1-purity); width=0.25+5*(1-purity)+particle_size/0.001',
      'melt_fraction=max(previous,logistic((T_sample-Tm_proxy)/width)); transmission=(0.05+0.9*melt_fraction)*(1-0.85*degraded)',
      'known-host-decomposition only: degraded=1-(1-degraded)*exp(-dt/30) when T_sample>=threshold',
      'input_mass=retained_mass+removed_mass; retained_mass=intact_mass+degraded_mass (no reaction stoichiometry claimed)',
      'terminal analysis: heat to T(start); capillaries inserted at T(start) start at 293.15 K; waiting time; ramp; melting point = clear point (90 %), range = onset (10 %) to clear',
      'furnace sensor: shown_C=a*(T_C*(1+gain)+offset)+b; the heater drives the shown value to the target; adjustment fits a, b to the calibration means vs nominal values'],
      parameters={'cooling_tau':dict(value=150.,unit='s',meaning_zh='温差相关冷却代理，不把厂家最短330s当固定工况',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},
      source_ids=['thermal_templates','simulation_assumption'],limitations=['未标定纯度/颗粒/热滞后系数；不输出纯度检测结果。','分解阈值未知时不推断；使用宿主提供有来源的阈值仍仅为暗化代理，未求化学反应和气体逸出。','熔程和透光曲线synthetic，视频未实现；非DSC热流模型。'])
    _ann.annotate_export('mt_mp70',caps,slots,{'capillary_melting':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'capillary_melting':mechanism})

"""CHEM-060 Ignite NIN0001; finite fluids, honest unknown chip/particle physics."""
from __future__ import annotations
import copy, datetime, hashlib, importlib.util, json, math, os, sys, tempfile
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
    if 'anyOf' in s:                                                       # terminal schemas (nullable fields)
        for o in s['anyOf']:
            try:validate(o,v);return
            except ActionRejected:pass
        raise ActionRejected('INVALID_ARGUMENT')
    t=s.get('type')
    if t=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
    if t in ('number','integer'):
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ActionRejected('INVALID_ARGUMENT')
        if t=='integer' and type(v) is not int:raise ActionRejected('INVALID_ARGUMENT')
        if not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
    elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
    elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
    elif t=='object':
        if not isinstance(v,dict) or not set(s.get('required',[]))<=v.keys():raise ActionRejected('INVALID_ARGUMENT')
        for k,x in v.items():
            if 'properties' not in s:continue                              # free-form object (terminal views)
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


# ---------------------------------------------------------------- touch-screen software (terminal layer; NanoAssemblr Ignite Operating Instructions NIN1134 AA, 'manual p.N')
# Main menu (information bar, heater control icon, cartridge indicator), Recipes (10 slots: edit, run, delete), Quick Run, the formulation screen
# (PRIME, START, STOP, RESET, status, progress), History (last 800 runs, re-run), Settings (Date & Time, Syringes, Software version).
ASSET_VERSION='1.1.2'
ASSET_DIR=ROOT/'assets/expansion_batch04/t5_automation/cytiva_nanoassemblrignite'
SOFTWARE_VERSION='1.0.0'                                                   # not published (assumption)
CLOCK_START='2026-01-01T00:00:00'                                         # instrument clock at simulation time 0 (assumption)
RECIPE_SLOTS,HISTORY_MAX,NAME_MAX=10,800,32                               # 10 numbered slots, last 800 runs (manual p.30, p.32); name length assumed
BRANDS={'BD':'BD','BI':'B Braun Injekt','BO':'B Braun Omnifix','TE':'Terumo','SA':'Shinva Ande','KD':'KDL','WE':'WEGO','KO':'KOVAX'}
_CR={'BD':[1,3,5,10],'BI':[1,5,10],'BO':[1,3,5,10],'TE':[1,3,5,10],'SA':[1,3,5,10],'KD':[1,5,10],'WE':[5,10],'KO':[1]}   # Ignite table p.38 (2 / 2.5 mL omitted)
_L={'BD':[20,30],'BI':[20],'BO':[20,30],'TE':[20,30]}                    # 20 / 30 mL dilution syringes (manual p.57, p.60)
SYRINGE_ID_M={1:4.7e-3,3:9.0e-3,5:12.0e-3,10:14.5e-3,20:19.1e-3,30:21.7e-3}   # nominal inner diameters per size (assumption; brand-independent)
SYRINGE_TYPES={f'{b}{v}':dict(id=f'{b}{v}',brand=BRANDS[b],size_ml=v,inner_diameter_m=SYRINGE_ID_M[v],channels=(['L','C','R'] if v<=10 else ['L']))
               for b in BRANDS for v in _CR[b]+_L.get(b,[])}               # two-letter brand code + volume (manual p.57)
PARAM=obj({'syringe_l':{'anyOf':[TEXT,{'type':'null'}]},'syringe_c':{'anyOf':[TEXT,{'type':'null'}]},'syringe_r':{'anyOf':[TEXT,{'type':'null'}]},
           'flow_ratio':{'anyOf':[number('1'),{'type':'null'}]},'total_volume_ml':{'anyOf':[number('mL'),{'type':'null'}]},'total_flow_ml_min':{'anyOf':[number('mL/min'),{'type':'null'}]},
           'dilution_ratio':{'anyOf':[number('1'),{'type':'null'}]},'start_waste_ml':{'anyOf':[number('mL'),{'type':'null'}]},'end_waste_ml':{'anyOf':[number('mL'),{'type':'null'}]}})
PARAM_EXAMPLE=dict(syringe_l='BD10',syringe_c='BD3',syringe_r='BD1',flow_ratio=3.,total_volume_ml=2.,total_flow_ml_min=4.,dilution_ratio=1.,start_waste_ml=.2,end_waste_ml=.05)
FREE={'type':'object'}
TCAPS={}
def tcap(k,i,o,c,z,ex,effects,kind='device_command'):TCAPS[k]=(i,o,c,z,kind,'agent',ex,list(effects))
tcap('recipe.save',obj({'slot':dict(type='integer',minimum=1,maximum=RECIPE_SLOTS,**{'x-unit':'1'}),'name':TEXT,'params':PARAM}),obj({'slot':dict(type='integer',**{'x-unit':'1'})}),['touchscreen'],
     'Recipes > Edit > Save：在1–10号槽保存命名配方（针筒L/C/R、流量比、总体积、总流量、稀释比、起始/结束废液）；参数须通过校验',dict(slot=1,name='LNP A',params=PARAM_EXAMPLE),['term.recipes'])
tcap('recipe.delete',obj({'slot':dict(type='integer',minimum=1,maximum=RECIPE_SLOTS,**{'x-unit':'1'})}),obj({'slot':dict(type='integer',**{'x-unit':'1'})}),['touchscreen'],
     'Recipes > Delete > Yes：清空该配方槽',dict(slot=1),['term.recipes'])
tcap('run.load',obj({'source':dict(type='string',enum=['quick','recipe','history']),'name':TEXT,'params':PARAM}),obj({'name':TEXT,'channels':FREE}),['touchscreen'],
     'Quick Run > Next / Recipes > Run / History > Run：校验参数并按所选针筒设定配制方法（method.configure），进入配制画面',dict(source='quick',name='Quick Run',params=PARAM_EXAMPLE),
     ['term.run','method.configured','flow.ratio_c_r','cartridge.state'])
tcap('settings.set_datetime',obj({'date':TEXT,'hour':dict(type='integer',minimum=1,maximum=12,**{'x-unit':'1'}),'minute':dict(type='integer',minimum=0,maximum=59,**{'x-unit':'1'}),
                                   'second':dict(type='integer',minimum=0,maximum=59,**{'x-unit':'1'}),'pm':BOOL}),obj({'datetime':TEXT}),['touchscreen'],
     'Settings > Date & Time：日历选日期，三个滚轮选时分秒，PM复选框，Save立即生效',dict(date='2026-05-01',hour=9,minute=30,second=0,pm=False),['term.datetime'])
tcap('settings.set_syringes',obj({'enabled':dict(type='array',items=TEXT,maxItems=len(SYRINGE_TYPES))}),obj({'enabled':dict(type='array',items=TEXT)}),['touchscreen'],
     'Settings > Syringes：勾选在参数下拉列表中显示的兼容针筒（品牌两字母代码+体积）',dict(enabled=['BD1','BD3','BD10']),['term.syringes_enabled'])
TERMINAL_CAPS=list(TCAPS)
for k,s_,z in [('term.recipes',dict(type='array',items={'anyOf':[FREE,{'type':'null'}]}),'10个配方槽（空槽为null）：名称与参数'),('term.history',dict(type='array',items=FREE),'最近800次配制（新到旧）：名称、时间、参数、结果SUCCESS/INCOMPLETE、芯片类型'),
               ('term.run',{'anyOf':[FREE,{'type':'null'}]},'配制画面载入的运行：名称、参数、各通道体积与流量、所需芯片类型'),('term.syringes_enabled',dict(type='array',items=TEXT),'参数下拉列表中显示的针筒'),
               ('term.datetime',TEXT,'仪器本地日期时间（ISO）'),('cartridge.state',dict(type='string',enum=['none','detected','unacceptable','acceptable']),'芯片指示：无/已检测/不适用（类型错或已用）/适用'),
               ('status.indicator',BOOL,'前部状态灯（通电时亮）')]:
    slot(k,s_,'1',z,c='status_indicator' if k=='status.indicator' else 'touchscreen')


def channel_values(p):
    """Calculated volumes and flow rates per channel shown under the syringe lists (manual p.70): C:R = flow ratio, L = dilution ratio x (C+R)."""
    r=p.get('flow_ratio') or 0.;v=p.get('total_volume_ml') or 0.;q=p.get('total_flow_ml_min') or 0.;d=p.get('dilution_ratio') or 0.;sw=p.get('start_waste_ml') or 0.
    fc=r/(1+r) if r else 0.;fr=1/(1+r) if r else 0.
    return {'L':dict(volume_ml=v*d,flow_ml_min=q*d,start_waste_ml=sw*d),'C':dict(volume_ml=v*fc,flow_ml_min=q*fc,start_waste_ml=sw*fc),
            'R':dict(volume_ml=v*fr,flow_ml_min=q*fr,start_waste_ml=sw*fr)}


def validate_params(p,enabled=None):
    """Parameter validation (manual p.59-67, p.110): missing fields, the Ignite ranges, the dispense volume against the syringe size; the messages
    are shown in red at the bottom of the screen."""
    validate(PARAM,p);err=[]
    for k,t in [('syringe_c','C syringe'),('syringe_r','R syringe'),('flow_ratio','Flow rate ratio'),('total_volume_ml','Total volume'),('total_flow_ml_min','Total flow rate'),
                ('start_waste_ml','Start waste'),('end_waste_ml','End waste')]:
        if p.get(k) is None:err.append(f'{t} is missing')
    if (p.get('syringe_l') is None)!=(not p.get('dilution_ratio')):err.append('Some fields are missing')        # Dilution ratio with the L syringe only
    for k in ('syringe_l','syringe_c','syringe_r'):
        x=p.get(k)
        if x is not None and (x not in SYRINGE_TYPES or k[-1].upper() not in SYRINGE_TYPES[x]['channels'] or (enabled is not None and x not in enabled)):err.append(f'{k[-1].upper()} syringe type is not available')
    if err:return err
    r,v,q,d=p['flow_ratio'],p['total_volume_ml'],p['total_flow_ml_min'],p.get('dilution_ratio') or 0.
    if not 1<=r<=10:err.append('Flow rate ratio must be 1:1 to 10:1')
    if not 1<=v<=20:err.append('Total volume must be 1 to 20 mL')
    if not (.1<=q<20):err.append('Total flow rate must be 0.1 to below 20 mL/min')
    if d and not d<=10:err.append('Dilution ratio must be up to 10:1')
    if d and not .1<=d*q<=48:err.append('Dilution flow rate must be 0.1 to 48 mL/min')
    if not 0<=p['start_waste_ml']<=v:err.append('Start waste must be 0 mL to Total volume')
    if p['end_waste_ml']<0:err.append('End waste must be 0 mL or more')
    if not err and p['start_waste_ml']+p['end_waste_ml']>v+1e-12:err.append('Start and end waste exceed the Total volume')
    for ch,val in channel_values(p).items():
        k='syringe_'+ch.lower()
        if p.get(k) and val['volume_ml']>SYRINGE_TYPES[p[k]]['size_ml']+1e-9:err.append(f'{ch} volume exceeds the syringe size')
    return err

# ---------------------------------------------------------------- front-panel hooks (parameter screen calculations, lists, heater dialog)
PARAM_KEYS=list(PARAM['properties'])
def _buf(panel,fid):
    f=panel.ui['forms'].get(fid);return dict(f['buffer']) if f else {}
def _params_of(panel):
    b=_buf(panel,'f_params');return {k:b.get(k) for k in PARAM_KEYS}
def _hk_params(panel,ctx,s):
    """The parameter form as run.load / recipe.save arguments."""
    return _params_of(panel)
def _hk_chvals(panel,ctx,s):
    """Per-channel volume and flow under the syringe lists, start waste per syringe (manual p.70)."""
    p=_params_of(panel);v=channel_values(p);rows=[]
    for ch in 'LCR':
        if p.get('syringe_'+ch.lower()) is None and ch=='L':continue
        rows.append(f'{ch}: {v[ch]["volume_ml"]:.2f} mL  {v[ch]["flow_ml_min"]:.2f} mL/min  waste {v[ch]["start_waste_ml"]:.2f} mL')
    return '\n'.join(rows)
def _hk_perr(panel,ctx,s):
    """Validation messages in red at the bottom of the parameter screen (manual p.67)."""
    try:err=validate_params(_params_of(panel),panel.host.term['enabled'])
    except ActionRejected:err=['A value is not valid']
    return '\n'.join(err[:4])
def _hk_syr_opts(panel,ctx,s):
    """Syringe drop-down of a channel: the enabled syringe types allowed on that channel (Settings > Syringes)."""
    return [dict(id=k,name=k) for k in panel.host.term['enabled'] if s['channel'] in SYRINGE_TYPES[k]['channels']]
def _hk_recipes(panel,ctx,s):
    out=[]
    for i,r in enumerate(panel.host.term['recipes']):
        if r is None:out.append(dict(slot=i+1,name='(empty)',summary='',empty=True));continue
        p=r['params'];out.append(dict(slot=i+1,name=r['name'],empty=False,
                                      summary=f'{p["total_volume_ml"]:g} mL  {p["total_flow_ml_min"]:g} mL/min  {p["flow_ratio"]:g}:1'+(f'  L {p["dilution_ratio"]:g}:1' if p.get('dilution_ratio') else '')))
    return out
HISTORY_PAGE=8
def _hk_history(panel,ctx,s):
    """One page of the History menu (newest first; navigation arrows change the page)."""
    page=int(panel.ui['vars'].get('hpage') or 0);rows=panel._state()['term.history'];out=[]
    for j,e in enumerate(rows[page*HISTORY_PAGE:(page+1)*HISTORY_PAGE]):
        p=e['params'] or {};out.append(dict(index=page*HISTORY_PAGE+j,name=e['name'],time=e['time'].replace('T',' ')[:16],result=e['result'],cartridge=e['cartridge'],
                                            summary=(f'{p.get("total_volume_ml",0):g} mL {p.get("total_flow_ml_min",0):g} mL/min' if p else '')))
    return out
def _hk_hist_pages(panel,ctx,s):
    n=len(panel._state()['term.history']);return max(1,-(-n//HISTORY_PAGE))
def _hk_syringe_rows(panel,ctx,s):
    sel=set(panel.ui['vars'].get('syr_sel') or [])
    return [dict(id=k,text=('[x] ' if k in sel else '[ ] ')+k,brand=SYRINGE_TYPES[k]['brand']) for k in sorted(SYRINGE_TYPES,key=lambda x:(x[:2],SYRINGE_TYPES[x]['size_ml']))]
def _hk_syr_toggle(panel,ctx,s):
    sel=list(panel.ui['vars'].get('syr_sel') or []);k=(ctx.get('row') or {}).get('id')
    if k in sel:sel.remove(k)
    elif k:sel.append(k)
    panel.ui['vars']['syr_sel']=sorted(sel);return True
def _hk_heat(panel,ctx,s):
    """Heater Control dialog (manual p.85): C and R sliders (Off or up to 75 C), Link sets both to the moved one; the setpoints go to heating.set."""
    b=_buf(panel,'f_heat');h=panel.host;amb=(h.plant['ambient_k'] if h.plant else 298.15)-273.15
    c,r=b.get('c_set'),b.get('r_set')
    if b.get('link'):
        moved=s.get('moved');c=r=(c if moved=='c' else r) if moved else (c if c is not None else r)
    on=c is not None or r is not None;tk=[(x if x is not None else amb)+273.15 for x in (c,r)]
    return dict(enabled=on,targets_k=[max(t,amb+273.15) for t in tk])[s['at_key']]
def _hk_heat_link(panel,ctx,s):
    """Link ticked: the other slider follows the one that was moved."""
    f=panel.ui['forms'].get('f_heat')
    if f and f['buffer'].get('link'):f['buffer']['r_set' if s['moved']=='c' else 'c_set']=f['buffer'][s['moved']+'_set']
    return True
def _hk_steps(panel,ctx,s):
    """Final actions listed on the formulation screen (manual p.87-101): cartridge, syringes, tubes, lid."""
    h=panel.host;r=h.term['run']
    if r is None:return ''
    v=r['channels'];out=[]
    need='NxGen Dilution' if r['dilution'] else 'NxGen'
    out.append(('[x] ' if h._cartridge_state()=='acceptable' else '[ ] ')+f'Insert a new {need} cartridge')
    for i,ch in enumerate('LCR'):
        sel=r['params'].get('syringe_'+ch.lower())
        if sel:out.append(('[x] ' if h.syringes[i] else '[ ] ')+f'Load {ch}: {sel} with {v[ch]["volume_ml"]:.2f} mL')
    out.append(('[x] ' if h.tubes else '[ ] ')+'Insert the sample and waste tubes')
    out.append(('[x] ' if not h.block else '[ ] ')+'Rotate the block down')
    out.append(('[x] ' if not h.lid else '[ ] ')+'Close the lid')
    return '\n'.join(out)
def _hk_ferr(panel,ctx,s):
    """Error messages of the formulation screen (manual p.110)."""
    h=panel.host;r=h.term['run'];out=[]
    if h.stage=='interrupted':out.append('Instrument is in safe mode: close the lid and tap Reset')
    if r is not None and h.phase!='running':
        if h.cartridge is None or h.lid or h.block:out.append('Cannot formulate')
        miss=[ch for i,ch in enumerate('LCR') if r['params'].get('syringe_'+ch.lower()) and not h.syringes[i]]
        if miss:out.append(', '.join(miss)+' syringe is missing or Paddle may need cleaning')
    return '\n'.join(out)
def _hk_status(panel,ctx,s):
    """Status text (manual p.102): Working, Interrupted, Completed."""
    h=panel.host
    if h.stage=='interrupted':return 'Interrupted'
    if h.phase=='running':return {'priming':'Priming','resetting':'Resetting'}.get(h.stage,'Working')
    if h.phase=='succeeded' and h.task=='acquisition.start':return 'Completed'
    return 'Ready'
def _hk_progress(panel,ctx,s):
    h=panel.host
    if not h.method or h.task!='acquisition.start':return 0.
    return min(1.,h.dispensed/h.method['volume_m3']) if h.method['volume_m3'] else 0.
def _hk_summary(panel,ctx,s):
    r=panel.host.term['run']
    if r is None:return ''
    p=r['params'];return (f'Flow rate ratio {p["flow_ratio"]:g}:1   Total volume {p["total_volume_ml"]:g} mL\nTotal flow rate {p["total_flow_ml_min"]:g} mL/min'
                          +(f'   Dilution {p["dilution_ratio"]:g}:1' if p.get('dilution_ratio') else '')+f'\nStart waste {p["start_waste_ml"]:g} mL   End waste {p["end_waste_ml"]:g} mL')
def _hk_history_entry(panel,ctx,s):
    """The History entry selected for Run: its name and parameters."""
    k=panel.ui['vars'].get('hsel');rows=panel.host.term['history']
    e=rows[int(k)] if k is not None and int(k)<len(rows) else None
    return None if e is None else {'name':e['name'],'params':copy.deepcopy(e['params'])}[s['at_key']]
def _hk_clock12(panel,ctx,s):
    """Date & Time rollers: the instrument clock as 12-hour hour, minute, second and the PM box."""
    t=datetime.datetime.fromisoformat(panel.host._clock())
    return dict(hour=t.hour%12 or 12,minute=t.minute,second=t.second,pm=t.hour>=12)[s['at_key']]
PANEL_HOOKS={'history_entry':_hk_history_entry,'clock12':_hk_clock12,'params':_hk_params,'chvals':_hk_chvals,'perr':_hk_perr,'syr_opts':_hk_syr_opts,'recipes':_hk_recipes,'history':_hk_history,'hist_pages':_hk_hist_pages,
             'syringe_rows':_hk_syringe_rows,'syr_toggle':_hk_syr_toggle,'heat_link':_hk_heat_link,'heat':_hk_heat,'steps':_hk_steps,'ferr':_hk_ferr,'status':_hk_status,'progress':_hk_progress,
             'summary':_hk_summary}


def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.cytiva_nanoassemblrignite (tests, Matterix bridge)
        return engine
    except ImportError:                                                      # loaded from its file path
        if '_chemsim_panel_ui' not in sys.modules:
            spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec);spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine()
if os.environ.get('IGNITE_PANEL_BUILD'):                                   # the spec builder imports this module before the spec exists
    PANEL_SPEC=dict(spec_version=_panel_ui.SPEC_VERSION,inputs={},displays={'screen':dict(kind='touch_tft',size=[480,800])},screens={'off':dict(style='static')})
else:
    PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS)|set(TCAPS))
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC)
PANEL_GESTURES=[k for k in PANEL_CAPS if k not in ('panel.capture','panel.frame')]
ALL_CAPS=list(CAPS)+list(TCAPS)+list(PANEL_CAPS)                            # instrument commands, touch-screen software, front panel
def _cap_vis(k):return CAPS[k][5] if k in CAPS else TCAPS[k][5] if k in TCAPS else PANEL_CAPS[k]['visibility']
def _cap_in(k):return CAPS[k][0] if k in CAPS else TCAPS[k][0] if k in TCAPS else PANEL_CAPS[k]['input_schema']

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
        self._caller=None                                                  # session principal while a session call runs (panel gestures act as that caller)
        self.scale=[1.,1.,1.]                                              # actual / commanded flow per channel (selected vs loaded syringe)
        self.term=dict(recipes=[None]*RECIPE_SLOTS,history=[],run=None,enabled=sorted(SYRINGE_TYPES),clock_offset_s=0.,last_op=None)
        self.power_epoch=0;self.powered_since=0.;self.panel_capture_dir=None;self.panel=_panel_ui.PanelUI(PANEL_SPEC,self,hooks=PANEL_HOOKS)
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return CytivaNanoassemblrigniteSession(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ('templates','panel')})
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
        v.update(self._terminal_values())
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def invoke(self,capability_id,**a):
        if capability_id in TCAPS or capability_id in PANEL_CAPS:return self._tinvoke(capability_id,a)
        if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability_id][0],a);old=self.debug_snapshot()
        try:
            result=self._invoke(capability_id,a);validate(CAPS[capability_id][1],result)
            if CAPS[capability_id][4]!='query':self.revision+=1;self.log.append(dict(time_s=self.time_s,event=capability_id,phase=self.phase,stage=self.stage))
            return copy.deepcopy(result)
        except Exception:
            templates,panel=self.templates,self.panel;self.__dict__.clear();self.__dict__.update(old);self.templates=templates;self.panel=panel;raise
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
            if a['enabled'] and not self.powered:self.power_epoch+=1;self.powered_since=self.time_s
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
            self.method=copy.deepcopy(a);self.term['run']=None;self.scale=[1.,1.,1.];return dict(configured=True)
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
                self.scale=self._flow_scale()
                v=self.method['volume_m3'];r=self.method['flow_ratio_c_r'];need=[v*d*self.scale[0],v*r/(1+r)*self.scale[1],v/(1+r)*self.scale[2]]   # scale: selected vs loaded syringe
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
            self._freeze_history();self.phase='running';self.elapsed_s=0.;self._operation_count+=1;self.operation_id=self.instance_id+':op:'+str(self._operation_count)
            if k=='acquisition.start':self._record_run()
            return dict(phase=self.phase,operation_id=self.operation_id)
        raise ActionRejected('UNKNOWN_CAPABILITY')
    # ---------------------------------------------------------------- touch-screen software and the panel commands
    def _tinvoke(self,k,a):
        if k in PANEL_CAPS:return self._pinvoke(k,a)
        validate(TCAPS[k][0],a);old=self.debug_snapshot()
        try:
            out=getattr(self,'_t_'+k.replace('.','_'))(**a);validate(TCAPS[k][1],out);self.revision+=1;return copy.deepcopy(out)
        except Exception:
            templates,panel=self.templates,self.panel;self.__dict__.clear();self.__dict__.update(old);self.templates=templates;self.panel=panel;raise
    def _need_on(self):
        if not self.powered:raise ActionRejected('POWER_OFF')
    def _clock(self):
        return (datetime.datetime.fromisoformat(CLOCK_START)+datetime.timedelta(seconds=round(self.time_s+self.term['clock_offset_s']))).isoformat()
    def _t_recipe_save(self,slot,name,params):
        self._need_on()
        if not 1<=len(name.strip())<=NAME_MAX:raise ActionRejected('INVALID_NAME')
        if validate_params(params,self.term['enabled']):raise ActionRejected('PARAMETERS_INVALID')
        self.term['recipes'][slot-1]=dict(slot=slot,name=name.strip(),params=copy.deepcopy(params));return dict(slot=slot)
    def _t_recipe_delete(self,slot):
        self._need_on()
        if self.term['recipes'][slot-1] is None:raise ActionRejected('EMPTY_SLOT')
        self.term['recipes'][slot-1]=None;return dict(slot=slot)
    def _t_run_load(self,source,name,params):
        """Quick Run > Next, Recipes > Run, History > Run (manual p.87-89): the validated parameters become the method; the selected syringes set the
        pusher speeds (a loaded syringe of another size gives another flow, manual p.57)."""
        self._need_on()
        if self.phase=='running':raise ActionRejected('BUSY')
        if validate_params(params,None if source=='history' else self.term['enabled']):raise ActionRejected('PARAMETERS_INVALID')
        d=params.get('dilution_ratio') or 0.;ml=1e-6
        m=dict(flow_m3_s=params['total_flow_ml_min']*ml/60.,flow_ratio_c_r=float(params['flow_ratio']),volume_m3=params['total_volume_ml']*ml,
               dilution_ratio=float(d),start_waste_equiv_m3=params['start_waste_ml']*ml,end_waste_equiv_m3=params['end_waste_ml']*ml)
        validate(CAPS['method.configure'][0],m);self._invoke('method.configure',m)
        self.term['run']=dict(source=source,name=name or 'Quick Run',params=copy.deepcopy(params),channels=channel_values(params),dilution=bool(d));self.scale=self._flow_scale()
        return dict(name=self.term['run']['name'],channels=copy.deepcopy(self.term['run']['channels']))
    def _t_settings_set_datetime(self,date,hour,minute,second,pm):
        self._need_on()
        try:day=datetime.date.fromisoformat(date)
        except (TypeError,ValueError):raise ActionRejected('INVALID_DATETIME') from None
        h24=hour%12+(12 if pm else 0);want=datetime.datetime.combine(day,datetime.time(h24,minute,second))
        now=datetime.datetime.fromisoformat(self._clock());self.term['clock_offset_s']+=(want-now).total_seconds();return dict(datetime=self._clock())
    def _t_settings_set_syringes(self,enabled):
        self._need_on()
        if any(x not in SYRINGE_TYPES for x in enabled):raise ActionRejected('UNKNOWN_SYRINGE')
        self.term['enabled']=sorted(set(enabled));return dict(enabled=list(self.term['enabled']))
    def _flow_scale(self):
        """Actual / commanded flow per channel: the pusher speed follows the selected syringe, the volume moved follows the loaded one."""
        r=self.term['run'];out=[1.,1.,1.]
        if r is None:return out
        for i,ch in enumerate('LCR'):
            sel=r['params'].get('syringe_'+ch.lower())
            if sel and self.syringes[i]:out[i]=(self.syringes[i]['inner_diameter_m']/SYRINGE_TYPES[sel]['inner_diameter_m'])**2
        return out
    def _record_run(self):
        r=self.term['run'];h=self.term['history']
        h.insert(0,dict(name=r['name'] if r else 'Remote run',time=self._clock(),params=copy.deepcopy(r['params']) if r else None,operation_id=self.operation_id,
                        cartridge='NxGen Dilution' if self.cartridge['dilution'] else 'NxGen',result=None))
        del h[HISTORY_MAX:]
    def _freeze_history(self):
        for e in self.term['history']:
            if e['result'] is None and e['operation_id']==self.operation_id:e['result']=self._result_of(e)
    def _result_of(self,e):
        if e['result'] is not None:return e['result']
        if e['operation_id']!=self.operation_id:return 'INCOMPLETE'
        return 'SUCCESS' if (self.phase=='succeeded' and self.task=='acquisition.start') else ('RUNNING' if self.phase=='running' and self.stage!='interrupted' else 'INCOMPLETE')
    def _cartridge_state(self):
        """Cartridge indicator (manual p.31): unacceptable = wrong type for the loaded run or used before (only while a run is shown)."""
        if self.cartridge is None:return 'none'
        r=self.term['run']
        if r is None:return 'detected'
        return 'unacceptable' if (self.used and not (self.phase=='running' and self.task=='acquisition.start')) or r['dilution']!=bool(self.cartridge['dilution']) else 'acceptable'
    def _terminal_values(self):
        t=self.term
        return {'term.recipes':copy.deepcopy(t['recipes']),'term.history':[dict(e,result=self._result_of(e)) for e in t['history']],'term.run':copy.deepcopy(t['run']),
                'term.syringes_enabled':list(t['enabled']),'term.datetime':self._clock(),'cartridge.state':self._cartridge_state(),'status.indicator':self.powered}
    def panel_read(self):
        out=self.read('agent');out.update({'host.powered':self.powered,'host.power_epoch':self.power_epoch,'host.powered_since':self.powered_since,
                                           'host.running':self.phase=='running','host.heater':self._heater_view()})
        return out
    def _heater_view(self):
        """Heater control icon (manual p.30): setpoints row, actual row; blue background below 45 C, red border when on, red background above 45 C,
        orange within 5 C of the setpoint."""
        c=[t-273.15 for t in self.temperature];sp=[t-273.15 for t in self.targets]
        near=self.heating and all(abs(a-b)<=5. for a,b in zip(c,self.effective and [t-273.15 for t in self.effective]))
        return dict(on=self.heating,setpoints='' if not self.heating else f'C {sp[0]:.0f}  R {sp[1]:.0f}',actual=f'{c[0]:.0f}  {c[1]:.0f} °C',
                    background='orange' if near else ('red' if max(c)>45. else 'blue'),border='red' if self.heating else 'blue',
                    c_on=self.heating and self.targets[0]>(self.plant['ambient_k'] if self.plant else 298.15)+1e-9,r_on=self.heating and self.targets[1]>(self.plant['ambient_k'] if self.plant else 298.15)+1e-9,
                    c_set=round(sp[0]),r_set=round(sp[1]))
    def panel_invoke(self,capability_id,arguments):
        """A panel action calls the same command a direct call would.  A gesture made through a session acts as that session's caller: the session's
        ownership guard applies (STOP or power off of another agent's run is refused) and a run started from the screen belongs to the caller."""
        try:
            caller=self._caller
            if caller is not None:CytivaNanoassemblrigniteSession(self,caller)._guard(capability_id,arguments)
            out=self.invoke(capability_id,**arguments)
            if caller is not None and out.get('operation_id'):self._owners[out['operation_id']]=caller
            return out
        except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
    def panel_time(self):return self.time_s
    def _pinvoke(self,k,a):
        validate(PANEL_CAPS[k]['input_schema'],a)
        if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
        try:
            if k=='panel.touch':
                to=None if a.get('to_x') is None or a.get('to_y') is None else (int(a['to_x']),int(a['to_y']))
                out=dict(accepted=bool(self.panel.touch(a['display_id'],int(a['x']),int(a['y']),float(a.get('duration_s',.1)),to=to)))
            elif k=='panel.switch':out=dict(accepted=bool(self.panel.switch(a['input_id'],a['position'])))
            elif k=='panel.frame':return dict(frame=self.panel.frame())
            else:
                data,w,hh=self.panel.png_bytes(a['display_id']);sha=hashlib.sha256(data).hexdigest()
                d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id;d.mkdir(parents=True,exist_ok=True)
                path=d/f'{a["display_id"]}_{sha[:16]}.png'
                if not path.exists():path.write_bytes(data)
                return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=hh,sha256=sha))
        except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
        self.revision+=1;return out
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
                        m=self.method;q=m['flow_m3_s'];r=m['flow_ratio_c_r'];flows=[q*m['dilution_ratio']*self.scale[0],q*r/(1+r)*self.scale[1],q/(1+r)*self.scale[2]]
                        volumes=[min(v,f*h) for v,f in zip(self.remaining,flows)];self.mixing=1-math.exp(-self.plant['mixer_volume_m3']/q/self.plant['mixing_time_s'])
                    self._feed(volumes)
                    if max(self.remaining)<=1e-18:
                        self.stage='returning'
                        if self.task=='cartridge.prime':self.primed=True
            self.time_s+=h;left-=h
        if dt>0:self.revision+=1
        return self.read()

_CONTROL_GUARDED={'acquisition.stop','heating.set'}  # commands that end or change another agent's run (the lid and the power switch on the back are
                                                      # physical: not refused; the instrument reacts by its interlock / as to a power loss)


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
        m=export_cytiva_nanoassemblrignite_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
            if self._role=='agent' and _cap_vis(capability_id)!='agent':raise ActionRejected('ACCESS_DENIED')
            if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
            validate(_cap_in(capability_id),arguments);key=(self._principal,instance_id,request_id);digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
            if key in h._requests:
                old,result=h._requests[key]
                if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(result)
            self._guard(capability_id,arguments);h._caller=self._principal
            try:result=h.invoke(capability_id,**arguments)
            finally:h._caller=None
            op=result.get('operation_id')
            if op:h._owners[op]=self._principal
            response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None);h._requests[key]=(digest,copy.deepcopy(response));return response
        except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
    def _guard(self,capability_id,arguments):
        """An agent may not stop, switch off or change another agent's running formulation / priming / reset (direct call or panel STOP / power
        heater control): STOP and heater changes are refused; unowned runs and the evaluator are not restricted.  Opening the lid and the power switch
        on the back are physical actions and are not refused: the instrument itself interrupts the run (safe mode / power loss)."""
        h=self._host
        if self._role!='agent' or capability_id not in _CONTROL_GUARDED or h.phase!='running':return
        if capability_id=='power.set' and (arguments.get('enabled') or not h.powered):return
        if h._owners.get(h.operation_id) not in (None,self._principal):raise ActionRejected('ACCESS_DENIED')
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





ERR_ZH={'POWER_OFF':'未通电','BUSY':'正在配制','INVALID_NAME':'配方名称为空或过长','PARAMETERS_INVALID':'参数校验未通过（屏幕底部红字）','EMPTY_SLOT':'配方槽为空',
        'INVALID_DATETIME':'日期无效','UNKNOWN_SYRINGE':'未知针筒类型','INVALID_ARGUMENT':'参数无效','UNKNOWN_INSTANCE':'未知实例','UNKNOWN_CAPABILITY':'未知能力',
        'ACCESS_DENIED':'无权限','REQUEST_CONFLICT':'请求ID冲突','PRECONDITION_FAILED':'前提不满足','FLOW_STRICT_BOUNDARY':'总流量须小于20 mL/min',
        'DILUTION_FLOW_RANGE':'稀释流量须为0.1–48 mL/min','WASTE_RANGE':'起始与结束废液超过总体积'}
TERRORS={'recipe.save':['POWER_OFF','INVALID_NAME','PARAMETERS_INVALID'],'recipe.delete':['POWER_OFF','EMPTY_SLOT'],
         'run.load':['POWER_OFF','BUSY','PARAMETERS_INVALID','FLOW_STRICT_BOUNDARY','DILUTION_FLOW_RANGE','WASTE_RANGE'],'settings.set_datetime':['POWER_OFF','INVALID_DATETIME'],
         'settings.set_syringes':['POWER_OFF','UNKNOWN_SYRINGE']}
_PANEL_USES=_panel_ui.spec_capabilities(PANEL_SPEC)
PANEL_EFFECTS={'panel.touch':sorted({x for k in _PANEL_USES for x in (TCAPS[k][7] if k in TCAPS else [s_ for s_ in CAPS[k][7] if s_ in SLOTS] if k in CAPS else [])}),
               'panel.switch':['power.enabled','cycle.phase','cycle.stage','heating.enabled','status.indicator'],'panel.capture':[],'panel.frame':[]}


def _terminal_cap_meta(k,slots):
    """Contract entry of a touch-screen command or a panel command."""
    if k in TCAPS:inp,out,c,z,kind,vis,ex,fx=TCAPS[k];errs=TERRORS[k]
    else:
        d=PANEL_CAPS[k];inp,out,c,z,kind,vis,ex=d['input_schema'],d['output_schema'],d['component_ids'],d['function_zh'],d['kind'],d['visibility'],d['example_arguments'];fx=PANEL_EFFECTS[k];errs=['PRECONDITION_FAILED']
    query=kind=='query' or k in ('panel.capture','panel.frame')
    return dict(function_zh=z,kind=kind,visibility=vis,component_ids=list(c),availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
                implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
                binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.cytiva_nanoassemblrignite.CytivaNanoassemblrigniteSession.invoke',
                             invocation=f'session.invoke(instance_id="ignite_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
                preconditions=[] if query else [dict(description_zh='按操作说明的软件规则检查（通电、未在配制、参数校验）；被拒时状态不变',state_ids=['power.enabled','cycle.phase'],enforced_by='runtime',
                                                     check='CytivaNanoassemblrigniteRuntime._tinvoke')],
                state_effects=[dict(state_id=x,timing='immediate',description_zh='触屏软件命令立即改变的设定或状态') for x in fx if x in slots],
                observes=[],model_effects=dict(mode='none',description_zh='触屏软件层：只改变运行层状态，没有几何联动',binding_ids=[]),
                execution=dict(mode='synchronous',time_domain='simulation',completion_criterion='同步：成功时一次写入全部效果',completion_state_ids=[],timeout_s=None,cancellation='unsupported',
                               retry='same_request_id',resources=['touchscreen'],partial_failure_zh='被拒时状态不变'),
                errors=[dict(code=e,meaning_zh=ERR_ZH.get(e,e),side_effects='none') for e in dict.fromkeys(['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','REQUEST_CONFLICT']+errs)],
                example_arguments=copy.deepcopy(ex),verification=[],
                limitations=['触屏软件按NanoAssemblr Ignite操作说明（NIN1134 AA）建模；画面为平面控件，提示文字为自拟；软件版本号未公布（占位）。'],
                source_ids=['manufacturer_manual','simulation_assumption'])


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
    for k in list(TCAPS)+list(PANEL_CAPS):caps[k]=_terminal_cap_meta(k,slots)
    spec=importlib.util.spec_from_file_location('_ignite_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.annotate_export(SLUG,caps,slots,{'microfluidics':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'syringe.load':(SYRINGE,SYRINGE_EXAMPLES[1]),'method.configure':(METHOD,METHOD_EXAMPLE)})
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'microfluidics':mech})

